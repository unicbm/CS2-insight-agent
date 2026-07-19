/** @vitest-environment jsdom */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import LiteCutPropertyPanel from "./LiteCutPropertyPanel.jsx";

describe("LiteCut export inspector frame blending", () => {
  it("offers explicit off, 180-degree, and 360-degree modes", () => {
    const onOutputSettingsChange = vi.fn();
    render(
      <LiteCutPropertyPanel
        defaultTab="export"
        outputDirHint="C:\\Videos\\exports"
        outputFrameBlend="180"
        onOutputSettingsChange={onOutputSettingsChange}
        v1ClipCount={1}
      />,
    );

    expect(screen.getByRole("button", { name: /关闭/ }).getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByRole("button", { name: /自然 180°/ }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: /强 360°/ }).getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByText(/仅对高于导出帧率的高帧率源素材有效/)).toBeTruthy();
    expect(screen.getByText(/开启后会增加导出耗时/)).toBeTruthy();
    expect(screen.getByText(/倒放与速度曲线片段暂不应用帧合成/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /强 360°/ }));
    expect(onOutputSettingsChange).toHaveBeenCalledWith({ frame_blend: "360" });
  });
});
