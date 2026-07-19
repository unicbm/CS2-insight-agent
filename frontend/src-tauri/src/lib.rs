use std::{
    fs::{self, OpenOptions},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
};

use tauri::{AppHandle, Manager, RunEvent, WindowEvent};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Default)]
struct BackendProcess(Mutex<Option<Child>>);

fn move_if_absent(from: &Path, to: &Path) {
    if !from.exists() || to.exists() {
        return;
    }
    if let Some(parent) = to.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let _ = fs::rename(from, to);
}

fn migrate_legacy_layout(container: &Path, data_root: &Path) {
    let _ = fs::create_dir_all(data_root);
    move_if_absent(
        &container.join("cs2-insight.config.json"),
        &data_root.join("cs2-insight.config.json"),
    );
    for suffix in ["", "-wal", "-shm"] {
        let name = format!("cs2-insight.db{suffix}");
        move_if_absent(&container.join(&name), &data_root.join(name));
    }
    for directory in ["logs", ".cs2_config_backup", ".obs_config_backups"] {
        move_if_absent(&container.join(directory), &data_root.join(directory));
    }
}

fn has_existing_data(root: &Path) -> bool {
    root.join("data").exists()
        || root.join("cs2-insight.config.json").exists()
        || root.join("cs2-insight.db").exists()
}

fn writable_data_root(app: &AppHandle) -> Result<PathBuf, String> {
    let tauri_root = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("无法解析应用数据目录：{error}"))?;

    #[cfg(windows)]
    let container = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .and_then(|app_data| {
            ["CS2 Insight Agent", "cs2-insight-agent"]
                .into_iter()
                .map(|name| app_data.join(name))
                .find(|candidate| has_existing_data(candidate))
        })
        .unwrap_or(tauri_root);

    #[cfg(not(windows))]
    let container = tauri_root;

    let data_root = container.join("data");
    migrate_legacy_layout(&container, &data_root);
    fs::create_dir_all(data_root.join("logs"))
        .map_err(|error| format!("无法创建应用数据目录 {}：{error}", data_root.display()))?;
    Ok(data_root)
}

fn runtime_root(app: &AppHandle) -> Result<PathBuf, String> {
    let bundled_root = app
        .path()
        .resource_dir()
        .map_err(|error| format!("无法解析安装资源目录：{error}"))?;
    if bundled_root.join("backend/app/run_server.py").is_file()
        && bundled_root.join("python/python.exe").is_file()
    {
        return Ok(bundled_root);
    }
    if cfg!(debug_assertions) {
        return PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .map_err(|error| format!("无法解析开发目录：{error}"));
    }
    Ok(bundled_root)
}

fn python_executable(root: &Path) -> Option<PathBuf> {
    let candidates = if cfg!(debug_assertions) {
        vec![
            root.join(".venv/Scripts/python.exe"),
            root.join("python/python.exe"),
        ]
    } else {
        vec![root.join("python/python.exe")]
    };
    candidates.into_iter().find(|path| path.is_file())
}

fn append_desktop_log(logs_dir: &Path, message: &str) {
    let path = logs_dir.join("desktop.log");
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        use std::io::Write;
        let _ = writeln!(file, "{message}");
    }
}

fn start_backend(app: &AppHandle) -> Result<(), String> {
    let root = runtime_root(app)?;
    let python = python_executable(&root).ok_or_else(|| {
        format!(
            "未找到 Python 运行时。已检查 {}。",
            root.join("python/python.exe").display()
        )
    })?;
    let run_server = root.join("backend/app/run_server.py");
    if !run_server.is_file() {
        return Err(format!("未找到后端入口：{}", run_server.display()));
    }

    let data_root = writable_data_root(app)?;
    let logs_dir = data_root.join("logs");
    let backend_dir = root.join("backend");
    let bundle_data_dir = root.join("data");
    append_desktop_log(
        &logs_dir,
        &format!(
            "[desktop] starting backend: {} {}",
            python.display(),
            run_server.display()
        ),
    );

    let stdout = OpenOptions::new()
        .create(true)
        .append(true)
        .open(logs_dir.join("backend-stdio.log"))
        .map_err(|error| format!("无法打开后端日志：{error}"))?;
    let stderr = stdout
        .try_clone()
        .map_err(|error| format!("无法复制后端日志句柄：{error}"))?;

    let mut command = Command::new(&python);
    command
        .arg(&run_server)
        .current_dir(&backend_dir)
        .env("CS2_INSIGHT_PORT", "19871")
        .env("PYTHONNOUSERSITE", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONFAULTHANDLER", "1")
        .env(
            "CS2_INSIGHT_CONFIG",
            data_root.join("cs2-insight.config.json"),
        )
        .env("CS2_INSIGHT_LOG_DIR", &logs_dir)
        .env("CS2_INSIGHT_DATA_DIR", &data_root)
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));
    if bundle_data_dir.is_dir() {
        command.env("CS2_INSIGHT_BUNDLE_DATA_DIR", bundle_data_dir);
    }
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    let child = command
        .spawn()
        .map_err(|error| format!("无法启动 Python 后端：{error}"))?;
    let state = app.state::<BackendProcess>();
    *state
        .0
        .lock()
        .map_err(|_| "后端进程状态锁已损坏".to_string())? = Some(child);
    Ok(())
}

fn stop_backend(app: &AppHandle) {
    let state = app.state::<BackendProcess>();
    let Ok(mut guard) = state.0.lock() else {
        return;
    };
    let Some(mut child) = guard.take() else {
        return;
    };
    if child.try_wait().ok().flatten().is_some() {
        return;
    }

    #[cfg(windows)]
    {
        let mut taskkill = Command::new("taskkill");
        taskkill.args(["/pid", &child.id().to_string(), "/f", "/t"]);
        taskkill.creation_flags(CREATE_NO_WINDOW);
        let _ = taskkill.status();
    }
    let _ = child.kill();
    let _ = child.wait();
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(BackendProcess::default())
        .setup(|app| {
            if let Err(error) = start_backend(app.handle()) {
                app.dialog()
                    .message(format!(
                        "{error}\n\n请重新安装完整安装包，或查看应用数据目录中的日志。"
                    ))
                    .title("CS2 Insight Agent — 后端启动失败")
                    .kind(MessageDialogKind::Error)
                    .show(|_| {});
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build CS2 Insight Agent desktop shell");

    app.run(|handle, event| match event {
        RunEvent::WindowEvent {
            label,
            event: WindowEvent::CloseRequested { api, .. },
            ..
        } if label == "main" => {
            // Closing Tauri's last window does not necessarily terminate the
            // event loop. Exit explicitly so the app cannot remain headless
            // with the bundled Python backend still running.
            api.prevent_close();
            stop_backend(handle);
            handle.exit(0);
        }
        RunEvent::Exit | RunEvent::ExitRequested { .. } => stop_backend(handle),
        _ => {}
    });
}
