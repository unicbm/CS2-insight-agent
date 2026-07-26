import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useStartupInitialization } from "./useStartupInitialization";

const mocks = vi.hoisted(() => ({
  shouldCheck: vi.fn(),
  get: vi.fn(),
  fetchUpdateInfo: vi.fn(),
}));

vi.mock("../api/api", () => ({ default: { get: mocks.get } }));
vi.mock("../utils/shouldCheckAppUpdates", () => ({
  shouldCheckAppUpdates: mocks.shouldCheck,
}));

describe("useStartupInitialization", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.shouldCheck.mockResolvedValue(true);
    mocks.fetchUpdateInfo.mockResolvedValue(undefined);
    mocks.get.mockResolvedValue({ data: { config_ok: true } });
  });

  it("waits for the backend, then checks updates before config", async () => {
    const { result, rerender } = renderHook(
      ({ ready }) =>
        useStartupInitialization({
          backendReady: ready,
          fetchUpdateInfo: mocks.fetchUpdateInfo,
        }),
      { initialProps: { ready: false } },
    );

    expect(mocks.fetchUpdateInfo).not.toHaveBeenCalled();
    rerender({ ready: true });

    await waitFor(() => expect(result.current.startupInitDone).toBe(true));
    expect(mocks.fetchUpdateInfo).toHaveBeenCalledWith({ manual: false, awaitDismiss: true });
    expect(mocks.get).toHaveBeenCalledWith("/config/quick-check");
    expect(result.current.initialQuickCheckStatus).toEqual({ config_ok: true });
  });

  it("skips the updater outside a supported desktop build", async () => {
    mocks.shouldCheck.mockResolvedValue(false);
    const { result } = renderHook(() =>
      useStartupInitialization({
        backendReady: true,
        fetchUpdateInfo: mocks.fetchUpdateInfo,
      }),
    );

    await waitFor(() => expect(result.current.startupInitDone).toBe(true));
    expect(mocks.fetchUpdateInfo).not.toHaveBeenCalled();
    expect(mocks.get).toHaveBeenCalledOnce();
  });
});
