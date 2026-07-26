import { check } from "@tauri-apps/plugin-updater";
import { relaunch } from "@tauri-apps/plugin-process";

export type UpdateMode = "normal" | "force";
export type UpdateChoice = "install" | "defer";
export type UpdateStatus =
  | "checking"
  | "available"
  | "downloading"
  | "downloaded"
  | "not-available"
  | "error"
  | "cancelled";

export interface DesktopUpdateStatus {
  status: UpdateStatus;
  update_mode: UpdateMode;
  latest_version?: string | null;
  release_notes?: string;
  progress?: { percent: number };
  error?: string;
}

export interface DesktopUpdateController {
  start(): void;
  confirm(): void;
  defer(): void;
  cancel(): void;
}

type StatusListener = (status: DesktopUpdateStatus) => void;

/** Tauri 桌面壳注入 IPC 对象；浏览器 / Vite dev 页面无此对象。 */
export function isTauriDesktop(): boolean {
  return Boolean(window.__TAURI_INTERNALS__);
}

export function normalizeUpdateMode(value: unknown): UpdateMode {
  return String(value || "").trim().toLowerCase() === "force" ? "force" : "normal";
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Tauri updater 检查/下载控制器。
 *
 * 发现更新后会停在 available，等待 confirm() 再下载；defer()/cancel() 表示稍后再说。
 * force 模式下 defer/cancel 在开始下载前会被忽略。
 * Tauri 的 downloadAndInstall 无法中断进行中的下载。
 */
export function createDesktopUpdateCheck(onStatus?: StatusListener): DesktopUpdateController {
  let cancelled = false;
  let updateMode: UpdateMode = "normal";
  let confirmWait: ((choice: UpdateChoice) => void) | null = null;
  let startedDownload = false;

  const emit = (payload: DesktopUpdateStatus) => {
    try {
      onStatus?.(payload);
    } catch {
      // 状态回调异常不应中断更新流程
    }
  };

  const waitForUserChoice = () =>
    new Promise<UpdateChoice>((resolve) => {
      confirmWait = resolve;
    });

  const resolveChoice = (choice: UpdateChoice) => {
    if (!confirmWait) return false;
    if (updateMode === "force" && choice !== "install" && !startedDownload) {
      return false;
    }
    const resolve = confirmWait;
    confirmWait = null;
    resolve(choice);
    return true;
  };

  const run = async () => {
    emit({ status: "checking", update_mode: "normal" });

    let update;
    try {
      update = await check();
    } catch (error) {
      emit({ status: "error", error: errorMessage(error), update_mode: "normal" });
      return;
    }
    if (cancelled) {
      emit({ status: "cancelled", update_mode: "normal" });
      return;
    }
    if (!update) {
      emit({ status: "not-available", update_mode: "normal" });
      return;
    }

    updateMode = normalizeUpdateMode(update.rawJson["update_mode"]);
    const base = {
      latest_version: update.version || null,
      release_notes: typeof update.body === "string" ? update.body : "",
      update_mode: updateMode,
    };
    emit({ status: "available", ...base });

    const choice = await waitForUserChoice();
    if (cancelled || choice !== "install") {
      try {
        await update.close();
      } catch {
        // ignore
      }
      emit({ status: "cancelled", ...base });
      return;
    }

    startedDownload = true;
    let total = 0;
    let received = 0;
    try {
      await update.downloadAndInstall((event) => {
        if (event.event === "Started") {
          total = Number(event.data.contentLength) || 0;
          emit({ status: "downloading", ...base, progress: { percent: 0 } });
        } else if (event.event === "Progress") {
          received += Number(event.data.chunkLength) || 0;
          emit({
            status: "downloading",
            ...base,
            progress: { percent: total > 0 ? (received / total) * 100 : Number.NaN },
          });
        } else if (event.event === "Finished") {
          emit({ status: "downloaded", ...base });
        }
      });
    } catch (error) {
      emit({ status: "error", ...base, error: errorMessage(error) });
      return;
    }

    emit({ status: "downloaded", ...base });
    try {
      await relaunch();
    } catch {
      // 安装器已接管进程时 relaunch 可能失败，忽略
    }
  };

  return {
    start: () => {
      void run();
    },
    confirm: () => {
      resolveChoice("install");
    },
    defer: () => {
      cancelled = true;
      resolveChoice("defer");
    },
    cancel: () => {
      cancelled = true;
      resolveChoice("defer");
    },
  };
}
