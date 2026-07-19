import { describe, expect, it } from "vitest";
import { getObsVideoTarget, obsConfigHasIssues, obsEncoderIsHealthy } from "./obsConfigHealth.js";

function healthyStatus(overrides = {}) {
  return {
    obs_connected: true,
    recording_video_preset: "pro_4x3_480",
    monitor: { width: 3840, height: 2160 },
    video_target: { preset: "pro_4x3_480", width: 1280, height: 960, fps: 480 },
    video: { base_width: 1280, base_height: 960, output_width: 1280, output_height: 960, fps: 480 },
    scene: { dedicated_scene_exists: true, capture_source_exists: true, source_fit_to_canvas: true },
    recording: { encoder: "obs_nvenc_hevc_tex", format: "hybrid_mp4", rec_quality: "Advanced" },
    ...overrides,
  };
}

describe("OBS configuration health", () => {
  it("uses the selected 4:3 target instead of the monitor resolution", () => {
    const status = healthyStatus();
    expect(getObsVideoTarget(status)).toEqual({ preset: "pro_4x3_480", width: 1280, height: 960, fps: 480 });
    expect(obsEncoderIsHealthy(status)).toBe(true);
    expect(obsConfigHasIssues(status)).toBe(false);
  });

  it("flags wrong FPS and non-NVENC encoders for the pro preset", () => {
    expect(obsConfigHasIssues(healthyStatus({
      video: { base_width: 1280, base_height: 960, output_width: 1280, output_height: 960, fps: 60 },
    }))).toBe(true);
    expect(obsConfigHasIssues(healthyStatus({
      recording: { encoder: "obs_x264", format: "hybrid_mp4", rec_quality: "Advanced" },
    }))).toBe(true);
  });

  it("accepts standard recording at any frame rate of 60 or above", () => {
    const status = healthyStatus({
      recording_video_preset: "display",
      video_target: { preset: "display", width: 3840, height: 2160, fps: 144 },
      video: { base_width: 3840, base_height: 2160, output_width: 3840, output_height: 2160, fps: 144 },
      recording: { encoder: "obs_x264", format: "hybrid_mp4", rec_quality: "Advanced" },
    });
    expect(obsConfigHasIssues(status)).toBe(false);
  });
});
