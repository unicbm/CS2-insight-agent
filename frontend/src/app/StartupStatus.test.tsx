import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import StartupStatus from "./StartupStatus";

const labels: Record<string, string> = {
  "app.backendStarting": "正在启动本地分析引擎，请稍候",
  "app.startupCheckingConfig": "正在检查配置…",
  "app.startupCheckingUpdate": "正在检查版本更新…",
  "app.startupPreparing": "正在完成启动检查…",
};
const t = (key: string) => labels[key] ?? key;

describe("StartupStatus", () => {
  it("shows a localized, non-blocking backend status", () => {
    render(
      <StartupStatus
        backendReady={false}
        startupInitDone={false}
        startupInitPhase={null}
        standalonePreview={false}
        t={t}
      />,
    );

    const status = screen.getByRole("status");
    expect(status.textContent).toContain("正在启动本地分析引擎，请稍候");
    expect(status.className).toContain("pointer-events-none");
    expect(status.className).not.toContain("inset-0");
  });

  it("switches to the localized startup phase after the backend is ready", () => {
    render(
      <StartupStatus
        backendReady
        startupInitDone={false}
        startupInitPhase="config"
        standalonePreview={false}
        t={t}
      />,
    );

    expect(screen.getByRole("status").textContent).toContain("正在检查配置…");
  });

  it("disappears after startup work is complete", () => {
    const { container } = render(
      <StartupStatus
        backendReady
        startupInitDone
        startupInitPhase={null}
        standalonePreview={false}
        t={t}
      />,
    );

    expect(container.firstChild).toBeNull();
  });
});
