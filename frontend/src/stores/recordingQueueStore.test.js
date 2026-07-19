import { beforeEach, describe, expect, test } from "vitest";
import {
  RECORDING_QUEUE_STORAGE_KEY,
  useRecordingQueue,
} from "./recordingQueueStore.js";

function queueItem(id) {
  return {
    id,
    demoPath: `C:\\demos\\${id}.dem`,
    demoFilename: `${id}.dem`,
    clipId: `clip-${id}`,
    clientClipUid: `uid-${id}`,
    clipData: { client_clip_uid: `uid-${id}`, category: "highlight" },
  };
}

beforeEach(() => {
  localStorage.clear();
  useRecordingQueue.setState({
    queue: [],
    globalPacing: {},
    presetPacing: {},
    lastQueueSnapshot: null,
  });
});

describe("recording queue recovery", () => {
  test("persists only versioned queue state and pacing", () => {
    useRecordingQueue.getState().addToQueue(queueItem("one"));
    useRecordingQueue.getState().setGlobalPacing({ pre_first_sec: 3 });

    const saved = JSON.parse(localStorage.getItem(RECORDING_QUEUE_STORAGE_KEY));
    expect(saved.version).toBe(1);
    expect(saved.state.queue).toHaveLength(1);
    expect(saved.state.queue[0].id).toBe("one");
    expect(saved.state.globalPacing).toEqual({ pre_first_sec: 3 });
    expect(saved.state).not.toHaveProperty("presetPacing");
    expect(saved.state).not.toHaveProperty("lastQueueSnapshot");
  });

  test("clearQueue keeps one snapshot and undo restores it", () => {
    useRecordingQueue.getState().addToQueue([queueItem("one"), queueItem("two")]);

    const snapshot = useRecordingQueue.getState().clearQueue();
    expect(snapshot.queue.map((item) => item.id)).toEqual(["one", "two"]);
    expect(useRecordingQueue.getState().queue).toEqual([]);
    expect(useRecordingQueue.getState().lastQueueSnapshot.queue).toHaveLength(2);

    expect(useRecordingQueue.getState().undoClearQueue()).toBe(true);
    expect(useRecordingQueue.getState().queue.map((item) => item.id)).toEqual(["one", "two"]);
    expect(useRecordingQueue.getState().lastQueueSnapshot).toBeNull();
  });

  test("undo does not discard clips added after a clear", () => {
    useRecordingQueue.getState().addToQueue(queueItem("old"));
    useRecordingQueue.getState().clearQueue();
    useRecordingQueue.getState().addToQueue(queueItem("new"));

    useRecordingQueue.getState().undoClearQueue();

    expect(useRecordingQueue.getState().queue.map((item) => item.id)).toEqual(["old", "new"]);
  });
});
