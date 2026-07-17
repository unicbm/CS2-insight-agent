import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { useLocaleStore } from "../../i18n/localeStore.js";
import DemoWatchPathsModal from "./DemoWatchPathsModal";

describe("DemoWatchPathsModal", () => {
  beforeEach(() => {
    useLocaleStore.setState({ locale: "zh", effectiveLocale: "zh" });
    window.electron = {
      showOpenDialog: vi.fn().mockResolvedValue({
        canceled: false,
        filePaths: ["C:\\Users\\Uni\\Downloads\\cs2-demotracer-ready"],
      }),
    };
  });

  afterEach(() => {
    delete window.electron;
  });

  test("uses the native directory picker and only updates the path list after save succeeds", async () => {
    const onSaveConfig = vi.fn().mockResolvedValue({ ok: true });
    const onDemoWatchPathsChange = vi.fn();
    render(
      <DemoWatchPathsModal
        open
        onClose={vi.fn()}
        demoWatchPaths={[]}
        onDemoWatchPathsChange={onDemoWatchPathsChange}
        onSaveConfig={onSaveConfig}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "浏览目录" }));
    const input = screen.getByRole("textbox", { name: "Demo 扫描根目录" });
    await waitFor(() => expect(input.value).toBe("C:\\Users\\Uni\\Downloads\\cs2-demotracer-ready"));
    expect(window.electron.showOpenDialog).toHaveBeenCalledWith(
      expect.objectContaining({ properties: ["openDirectory"] }),
    );

    fireEvent.click(screen.getByRole("button", { name: "添加" }));
    await waitFor(() => expect(onSaveConfig).toHaveBeenCalledTimes(1));
    expect(onDemoWatchPathsChange).toHaveBeenCalledWith([
      "C:\\Users\\Uni\\Downloads\\cs2-demotracer-ready",
    ]);
    expect(screen.getByText(/路径配置已保存/)).toBeTruthy();
  });

  test("surfaces save errors and leaves the draft path available for retry", async () => {
    const onSaveConfig = vi.fn().mockResolvedValue({ ok: false, error: "disk denied" });
    const onDemoWatchPathsChange = vi.fn();
    render(
      <DemoWatchPathsModal
        open
        onClose={vi.fn()}
        demoWatchPaths={[]}
        onDemoWatchPathsChange={onDemoWatchPathsChange}
        onSaveConfig={onSaveConfig}
      />,
    );

    const input = screen.getByRole("textbox", { name: "Demo 扫描根目录" });
    fireEvent.change(input, { target: { value: "C:\\demos" } });
    fireEvent.click(screen.getByRole("button", { name: "添加" }));

    await screen.findByText("路径配置保存失败：disk denied");
    expect(input.value).toBe("C:\\demos");
    expect(onDemoWatchPathsChange).not.toHaveBeenCalled();
  });
});
