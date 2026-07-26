import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DesktopUpdateStatus } from "../utils/desktopUpdater";
import { useDesktopUpdater } from "./useDesktopUpdater";

const mocks = vi.hoisted(() => ({
  shouldCheck: vi.fn(),
  getVersion: vi.fn(),
  put: vi.fn(),
  createCheck: vi.fn(),
}));

vi.mock("@tauri-apps/api/app", () => ({ getVersion: mocks.getVersion }));
vi.mock("../api/api", () => ({ default: { put: mocks.put } }));
vi.mock("../utils/shouldCheckAppUpdates", () => ({
  shouldCheckAppUpdates: mocks.shouldCheck,
}));
vi.mock("../utils/desktopUpdater", () => ({
  createDesktopUpdateCheck: mocks.createCheck,
}));

describe("useDesktopUpdater", () => {
  let emit: (status: DesktopUpdateStatus) => void;
  const controller = {
    start: vi.fn(),
    confirm: vi.fn(),
    defer: vi.fn(),
    cancel: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.shouldCheck.mockResolvedValue(true);
    mocks.getVersion.mockResolvedValue("2.4.0");
    mocks.put.mockResolvedValue({});
    mocks.createCheck.mockImplementation((listener: typeof emit) => {
      emit = listener;
      return controller;
    });
  });

  it("opens a manual update check and forwards install confirmation", async () => {
    const { result } = renderHook(() => useDesktopUpdater((key) => key));

    await act(() => result.current.fetchUpdateInfo({ manual: true }));
    expect(result.current.updateModalOpen).toBe(true);
    expect(result.current.updateInfo?.current_version).toBe("2.4.0");
    expect(controller.start).toHaveBeenCalledOnce();

    act(() => {
      emit({
        status: "available",
        update_mode: "normal",
        latest_version: "2.5.0",
        release_notes: "notes",
      });
    });
    expect(result.current.updateInfo?.latest_version).toBe("2.5.0");

    act(() => result.current.handleUpdateConfirm());
    expect(controller.confirm).toHaveBeenCalledOnce();
  });

  it("allows deferring a normal update but not closing a forced update", async () => {
    const { result } = renderHook(() => useDesktopUpdater((key) => key));
    await act(() => result.current.fetchUpdateInfo({ manual: true }));

    act(() => {
      emit({ status: "available", update_mode: "normal", latest_version: "2.5.0" });
    });
    act(() => result.current.handleUpdateModalClose());
    expect(controller.defer).toHaveBeenCalledOnce();

    await act(() => result.current.fetchUpdateInfo({ manual: true }));
    act(() => {
      emit({ status: "available", update_mode: "force", latest_version: "2.5.0" });
    });
    act(() => result.current.handleUpdateModalClose());
    expect(result.current.updateModalOpen).toBe(true);
    expect(controller.defer).toHaveBeenCalledOnce();
  });

  it("reports dev mode for a manual browser check", async () => {
    mocks.shouldCheck.mockResolvedValue(false);
    const { result } = renderHook(() => useDesktopUpdater((key) => key));

    await act(() => result.current.fetchUpdateInfo({ manual: true }));
    expect(result.current.updateInfo).toMatchObject({
      status: "error",
      error: "settings.updateDevModeError",
    });
    expect(mocks.createCheck).not.toHaveBeenCalled();
  });
});
