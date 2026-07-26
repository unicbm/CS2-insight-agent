mod backend_process;
mod commands;

use std::thread;

use backend_process::{start_backend, stop_backend, BackendProcess};
use commands::{read_bootstrap_locale, read_legacy_ui_state};
use tauri::{Manager, RunEvent, WindowEvent};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .manage(BackendProcess::default())
        .invoke_handler(tauri::generate_handler![
            read_legacy_ui_state,
            read_bootstrap_locale
        ])
        .setup(|app| {
            // Start the backend on a worker thread so the window (and its
            // non-blocking startup status) appears immediately instead of
            // waiting for the Python process to answer HTTP.
            let handle = app.handle().clone();
            thread::spawn(move || {
                if let Err(error) = start_backend(&handle) {
                    handle
                        .dialog()
                        .message(format!(
                            "{error}\n\n请重新安装完整安装包，或查看应用数据目录中的日志。"
                        ))
                        .title("CS2 Insight Agent — 后端启动失败")
                        .kind(MessageDialogKind::Error)
                        .blocking_show();
                    handle.exit(1);
                }
            });
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
            // Destroy the webview first so EventSource/HTTP connections close
            // immediately. Otherwise uvicorn waits on the still-live renderer
            // while this handler waits on uvicorn.
            api.prevent_close();
            if let Some(window) = handle.get_webview_window(&label) {
                let _ = window.destroy();
            }
            // window.destroy() is only queued on the event loop; blocking on
            // the backend here would keep a frozen window on screen for the
            // whole graceful-shutdown wait. Stop the backend on a worker
            // thread so the window disappears instantly.
            let handle = handle.clone();
            thread::spawn(move || {
                stop_backend(&handle);
                handle.exit(0);
            });
        }
        RunEvent::ExitRequested { code, api, .. } => {
            // The last window closing must not tear down the process while the
            // worker thread is still stopping the backend; explicit exit()
            // calls (which carry a code) pass through.
            if code.is_none() {
                api.prevent_exit();
            }
        }
        RunEvent::Exit => stop_backend(handle),
        _ => {}
    });
}
