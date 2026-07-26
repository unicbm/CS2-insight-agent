use std::{fs, path::PathBuf};

#[tauri::command]
pub(crate) fn read_legacy_ui_state() -> Result<Option<String>, String> {
    #[cfg(windows)]
    {
        let app_data = std::env::var_os("APPDATA")
            .map(PathBuf::from)
            .ok_or_else(|| "Windows APPDATA 环境变量不存在".to_string())?;
        let state_file = app_data
            .join("CS2 Insight Agent")
            .join("data")
            .join("desktop-ui-state-v1.json");
        if !state_file.is_file() {
            return Ok(None);
        }
        fs::read_to_string(&state_file)
            .map(Some)
            .map_err(|error| format!("无法读取旧版界面状态 {}：{error}", state_file.display()))
    }

    #[cfg(not(windows))]
    Ok(None)
}
