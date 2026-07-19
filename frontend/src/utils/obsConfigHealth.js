/** OBS 配置中心健康检查（与 ObsConfigCenterPage 状态列表一致）。 */

export function getObsVideoTarget(status) {
  const target = status?.video_target;
  return {
    preset: target?.preset || status?.recording_video_preset || "display",
    width: target?.width ?? status?.monitor?.width ?? 0,
    height: target?.height ?? status?.monitor?.height ?? 0,
    fps: target?.fps ?? 60,
  };
}

export function obsEncoderIsHealthy(status) {
  const target = getObsVideoTarget(status);
  const encoder = String(status?.recording?.encoder || "").trim().toLowerCase();
  if (!encoder || ["none", "null", "stream", "use_stream_encoder"].includes(encoder)) return false;
  return target.preset === "pro_4x3_480" ? encoder.includes("nvenc") : true;
}

export function obsConfigHasIssues(status) {
  if (!status?.obs_connected) return false;
  const target = getObsVideoTarget(status);
  const fpsOk = target.preset === "pro_4x3_480"
    ? status.video?.fps === target.fps
    : Number(status.video?.fps || 0) >= 60;
  return !!(
    status.video?.base_width !== target.width ||
    status.video?.base_height !== target.height ||
    status.video?.output_width !== target.width ||
    status.video?.output_height !== target.height ||
    !fpsOk ||
    !status.scene?.dedicated_scene_exists ||
    !status.scene?.capture_source_exists ||
    !status.scene?.source_fit_to_canvas ||
    status.recording?.format !== "hybrid_mp4" ||
    status.recording?.rec_quality === "Stream" ||
    !obsEncoderIsHealthy(status)
  );
}
