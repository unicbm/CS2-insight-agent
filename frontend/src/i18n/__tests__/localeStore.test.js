import { describe, test, expect, beforeEach, vi } from "vitest";

const putMock = vi.fn(() => Promise.resolve({ data: {} }));
const invokeMock = vi.fn(() => Promise.resolve(null));
vi.mock("../../api/api", () => ({ default: { put: (...a) => putMock(...a) } }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a) => invokeMock(...a) }));

import {
  hydrateDesktopBootstrapLocale,
  useLocaleStore,
} from "../localeStore.js";

describe("localeStore", () => {
  beforeEach(() => {
    putMock.mockClear();
    invokeMock.mockClear();
    delete globalThis.__TAURI_INTERNALS__;
    useLocaleStore.getState().hydrate("zh");
  });

  test("默认 locale 为 zh", () => {
    expect(useLocaleStore.getState().locale).toBe("zh");
  });

  test("hydrate 从配置注入但不回写后端", () => {
    useLocaleStore.getState().hydrate("en");
    expect(useLocaleStore.getState().locale).toBe("en");
    expect(putMock).not.toHaveBeenCalled();
  });

  test("hydrate 非法值回退到 auto 并解析系统语言", () => {
    useLocaleStore.getState().hydrate("fr");
    expect(useLocaleStore.getState().locale).toBe("auto");
    expect(["zh", "en"]).toContain(useLocaleStore.getState().effectiveLocale);
  });

  test("setLocale 更新 state 并持久化到 config", () => {
    useLocaleStore.getState().setLocale("en");
    expect(useLocaleStore.getState().locale).toBe("en");
    expect(putMock).toHaveBeenCalledWith("config", { locale: "en" });
  });

  test("setLocale 非法值回退到 auto", () => {
    useLocaleStore.getState().setLocale("fr");
    expect(useLocaleStore.getState().locale).toBe("auto");
    expect(putMock).toHaveBeenCalledWith("config", { locale: "auto" });
  });

  test("Tauri 在 React 渲染前从本地配置注入语言", async () => {
    globalThis.__TAURI_INTERNALS__ = {};
    invokeMock.mockResolvedValueOnce("en");

    await hydrateDesktopBootstrapLocale();

    expect(invokeMock).toHaveBeenCalledWith("read_bootstrap_locale");
    expect(useLocaleStore.getState().locale).toBe("en");
  });

  test("浏览器模式不读取桌面配置", async () => {
    await hydrateDesktopBootstrapLocale();

    expect(invokeMock).not.toHaveBeenCalled();
    expect(useLocaleStore.getState().locale).toBe("zh");
  });
});
