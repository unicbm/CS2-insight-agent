import { describe, expect, test } from "vitest";

import { obsConfigHasAutoFixableIssues, obsConfigHasIssues } from "./obsConfigHealth";


function healthyStatus() {
  return {
    obs_connected: true,
    monitor: { width: 1920, height: 1080 },
    video: {
      base_width: 1920,
      base_height: 1080,
      output_width: 1920,
      output_height: 1080,
    },
    scene: {
      dedicated_scene_exists: true,
      capture_source_exists: true,
      source_fit_to_canvas: true,
    },
    audio: {
      ready: true,
      capture_audio_enabled: true,
      capture_muted: false,
      exclusive_track1: true,
      track1_isolated: true,
    },
    recording: {
      output_mode: "Advanced",
      output_track1_enabled: true,
      format: "hybrid_mp4",
      rec_quality: "Advanced",
    },
  };
}


describe("OBS config audio health", () => {
  test("accepts a healthy Advanced Output profile", () => {
    expect(obsConfigHasIssues(healthyStatus())).toBe(false);
  });

  test("flags a disconnected managed audio path", () => {
    const status = healthyStatus();
    status.audio.ready = false;
    expect(obsConfigHasIssues(status)).toBe(true);
  });

  test("flags Advanced Output when track 1 is not recorded", () => {
    const status = healthyStatus();
    status.recording.output_track1_enabled = false;
    expect(obsConfigHasIssues(status)).toBe(true);
    expect(obsConfigHasAutoFixableIssues(status)).toBe(true);
  });

  test("separates manual track conflicts from safe automatic repairs", () => {
    const status = healthyStatus();
    status.audio = {
      ready: false,
      capture_audio_enabled: true,
      capture_muted: false,
      exclusive_track1: true,
      track1_isolated: false,
      track1_conflict_names: ["Mic/Aux"],
    };

    expect(obsConfigHasIssues(status)).toBe(true);
    expect(obsConfigHasAutoFixableIssues(status)).toBe(false);
  });
});
