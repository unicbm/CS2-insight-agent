/** OBS 配置中心健康检查；录制门禁与活动设置页共用同一状态语义。 */

export function obsConfigHasIssues(status) {
  if (!status?.obs_connected) return false;
  const monW = status.monitor?.width;
  const monH = status.monitor?.height;
  return !!(
    status.video?.base_width !== monW ||
    status.video?.base_height !== monH ||
    status.video?.output_width !== monW ||
    status.video?.output_height !== monH ||
    !status.scene?.dedicated_scene_exists ||
    !status.scene?.capture_source_exists ||
    !status.scene?.source_fit_to_canvas ||
    !status.audio?.ready ||
    status.recording?.output_track1_enabled === false ||
    status.recording?.format !== "hybrid_mp4" ||
    status.recording?.rec_quality === "Stream"
  );
}

/** Problems that the app can safely repair without changing user-owned OBS inputs. */
export function obsConfigHasAutoFixableIssues(status) {
  if (!status?.obs_connected) return false;
  const monW = status.monitor?.width;
  const monH = status.monitor?.height;
  return !!(
    status.video?.base_width !== monW ||
    status.video?.base_height !== monH ||
    status.video?.output_width !== monW ||
    status.video?.output_height !== monH ||
    !status.scene?.dedicated_scene_exists ||
    !status.scene?.capture_source_exists ||
    !status.scene?.source_fit_to_canvas ||
    status.audio?.capture_audio_enabled !== true ||
    status.audio?.capture_muted !== false ||
    status.audio?.exclusive_track1 !== true ||
    status.recording?.output_track1_enabled === false ||
    status.recording?.format !== "hybrid_mp4" ||
    status.recording?.rec_quality === "Stream"
  );
}
