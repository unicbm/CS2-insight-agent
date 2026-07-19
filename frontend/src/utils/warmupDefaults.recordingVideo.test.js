import { describe, expect, it } from "vitest";

import {
  splitRecordWarmupConfirmPayload,
  warmupApiPayloadToPersisted,
  warmupUiOptsToPersisted,
} from "./warmupDefaults.js";

describe("per-recording video settings", () => {
  it("persists the selected FPS with the custom resolution", () => {
    expect(warmupUiOptsToPersisted({
      aspect_ratio: "4:3",
      resolution_width: "1920",
      resolution_height: "1440",
      recording_fps: "480",
    })).toMatchObject({
      aspect_ratio: "4:3",
      resolution_width: "1920",
      resolution_height: "1440",
      recording_fps: "480",
    });
  });

  it("keeps FPS in the warmup API payload", () => {
    const { warmupForApi } = splitRecordWarmupConfirmPayload({
      resolution_width: 1920,
      resolution_height: 1440,
      recording_fps: 480,
      obs_transition_enabled: true,
    });
    expect(warmupForApi).toEqual({
      resolution_width: 1920,
      resolution_height: 1440,
      recording_fps: 480,
    });
    expect(warmupApiPayloadToPersisted(warmupForApi).recording_fps).toBe("480");
  });
});
