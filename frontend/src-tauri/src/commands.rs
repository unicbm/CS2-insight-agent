use std::{fs, path::PathBuf};

fn locale_from_config(raw: &str) -> Option<String> {
    let body: serde_json::Value = serde_json::from_str(raw.trim_start_matches('\u{feff}')).ok()?;
    let locale = body.get("locale")?.as_str()?;
    matches!(locale, "auto" | "zh" | "en").then(|| locale.to_string())
}

#[cfg(windows)]
fn desktop_data_file(name: &str) -> Result<PathBuf, String> {
    let app_data = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .ok_or_else(|| "Windows APPDATA 环境变量不存在".to_string())?;
    Ok(app_data.join("CS2 Insight Agent").join("data").join(name))
}

#[tauri::command]
pub(crate) fn read_legacy_ui_state() -> Result<Option<String>, String> {
    #[cfg(windows)]
    {
        let state_file = desktop_data_file("desktop-ui-state-v1.json")?;
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

#[tauri::command]
pub(crate) fn read_bootstrap_locale() -> Result<Option<String>, String> {
    #[cfg(windows)]
    {
        let config_file = desktop_data_file("cs2-insight.config.json")?;
        if !config_file.is_file() {
            return Ok(None);
        }
        let raw = fs::read_to_string(&config_file)
            .map_err(|error| format!("无法读取界面语言配置 {}：{error}", config_file.display()))?;
        return Ok(locale_from_config(&raw));
    }

    #[cfg(not(windows))]
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::locale_from_config;

    #[test]
    fn reads_supported_bootstrap_locale() {
        assert_eq!(
            locale_from_config(r#"{"locale":"zh"}"#).as_deref(),
            Some("zh")
        );
        assert_eq!(
            locale_from_config(r#"{"locale":"en"}"#).as_deref(),
            Some("en")
        );
        assert_eq!(
            locale_from_config(r#"{"locale":"auto"}"#).as_deref(),
            Some("auto")
        );
    }

    #[test]
    fn ignores_invalid_bootstrap_locale() {
        assert_eq!(locale_from_config(r#"{"locale":"fr"}"#), None);
        assert_eq!(locale_from_config("not-json"), None);
    }
}
