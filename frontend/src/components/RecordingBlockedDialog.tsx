import type { MouseEvent } from "react";
import { ShieldAlert, X } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { useT } from "../i18n/useT.js";
import { API_ERROR_SUBTITLE_KEYS } from "../utils/apiErrorMessages.js";

const subtitleKeys = API_ERROR_SUBTITLE_KEYS as Record<string, string>;

function recordingBlockedSubtitleKey(message: string, errorCode: string | null): string {
  if (errorCode && subtitleKeys[errorCode]) return subtitleKeys[errorCode];

  const lowerMessage = message.toLowerCase();
  if (
    message.includes("分辨率") ||
    message.includes("屏幕比例") ||
    message.includes("宽高") ||
    message.includes("启动分辨率") ||
    message.includes("所选屏幕比例") ||
    message.includes("填写启动分辨率") ||
    lowerMessage.includes("resolution") ||
    lowerMessage.includes("aspect ratio")
  ) {
    return "dialog.recordBlockedSubResolution";
  }
  if (
    message.includes("GSI") ||
    message.includes("未就绪") ||
    message.includes("未进入游戏") ||
    lowerMessage.includes("not ready") ||
    lowerMessage.includes("did not enter")
  ) {
    return "dialog.recordBlockedSubGsi";
  }
  if (
    message.includes("正在运行") ||
    (message.includes("CS2") && message.includes("退出")) ||
    (lowerMessage.includes("cs2") && lowerMessage.includes("running"))
  ) {
    return "dialog.recordBlockedSubRunning";
  }
  if (message.includes("已有录制任务") || lowerMessage.includes("already in progress")) {
    return "dialog.recordBlockedSubAlreadyRecording";
  }
  if (isConfigBackupMessage(message, errorCode)) {
    return "dialog.recordBlockedSubConfigRestore";
  }
  return "dialog.recordBlockedSubDefault";
}

function isConfigBackupMessage(message: string, errorCode: string | null): boolean {
  if (
    errorCode === "RECORDING_CONFIG_RESTORE_REQUIRED" ||
    errorCode === "CONFIG_RESTORE_REQUIRED"
  ) {
    return true;
  }
  const lowerMessage = message.toLowerCase();
  return (
    message.includes("尚未恢复") ||
    message.includes("异常退出") ||
    message.includes("一键恢复") ||
    message.includes("玩家配置") ||
    (lowerMessage.includes("restore") && lowerMessage.includes("config"))
  );
}

interface RecordingBlockedDialogProps {
  message: string;
  errorCode?: string | null;
  configRecoveryNeeded?: boolean | null;
  povRecoveryNeeded?: boolean;
  onClose(): void;
}

export default function RecordingBlockedDialog({
  message,
  errorCode = null,
  configRecoveryNeeded = null,
  povRecoveryNeeded = false,
  onClose,
}: RecordingBlockedDialogProps) {
  const t = useT();
  const navigate = useNavigate();
  if (!message) return null;

  const unexpectedCs2Exit = errorCode === "RECORDING_CS2_EXITED";
  const subtitleKey = recordingBlockedSubtitleKey(message, errorCode);
  const showConfigLink =
    configRecoveryNeeded == null
      ? isConfigBackupMessage(message, errorCode)
      : configRecoveryNeeded;
  const closeOnBackdrop = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) onClose();
  };

  return (
    <div
      className="fixed inset-0 z-[120] flex items-center justify-center bg-black/70 px-4 py-6 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="recording-blocked-title"
      onClick={closeOnBackdrop}
    >
      <div className="relative w-full max-w-md overflow-hidden rounded-xl border border-cs2-border bg-cs2-bg-card shadow-2xl">
        <button type="button" onClick={onClose} className="absolute right-3 top-3 rounded-md p-1.5 text-cs2-text-muted hover:bg-cs2-bg-input/50 hover:text-cs2-text-secondary" aria-label={t("dialog.recordBlockedClose")}>
          <X className="h-4 w-4" />
        </button>

        <div className="flex items-start gap-3 border-b border-cs2-border px-5 py-4">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-cs2-accent/30 bg-cs2-accent/10 text-cs2-accent">
            <ShieldAlert className="h-5 w-5" />
          </div>
          <div className="min-w-0 pr-7">
            <h2 id="recording-blocked-title" className="text-sm font-bold text-cs2-text-primary">
              {t(unexpectedCs2Exit ? "dialog.recordingInterruptedTitle" : "dialog.recordBlockedTitle")}
            </h2>
            <p className="mt-1 text-[12px] leading-relaxed text-cs2-text-muted">{t(subtitleKey)}</p>
          </div>
        </div>

        <div className="px-5 py-4">
          <p className="text-sm leading-6 text-cs2-text-secondary whitespace-pre-wrap break-words">{message}</p>
        </div>

        <div className="flex flex-wrap justify-end gap-2.5 border-t border-cs2-border bg-cs2-bg-input/30 px-5 py-3">
          {showConfigLink ? (
            <button type="button" onClick={() => { onClose(); navigate("/player-game-config"); }} className="rounded-lg border border-cs2-accent/40 bg-cs2-accent/10 px-4 py-2 text-sm font-bold text-cs2-accent transition-colors hover:bg-cs2-accent/20">
              {t("dialog.recordBlockedGoConfig")}
            </button>
          ) : null}
          {unexpectedCs2Exit && povRecoveryNeeded ? (
            <button type="button" onClick={() => { onClose(); navigate("/params"); }} className="rounded-lg border border-cs2-accent/40 bg-cs2-accent/10 px-4 py-2 text-sm font-bold text-cs2-accent transition-colors hover:bg-cs2-accent/20">
              {t("dialog.recordBlockedGoPov")}
            </button>
          ) : null}
          <button type="button" onClick={onClose} className="rounded-lg bg-cs2-accent px-4 py-2 text-sm font-extrabold text-cs2-text-on-accent shadow-lg shadow-cs2-accent/20 transition-colors hover:bg-cs2-accent-light">
            {t("dialog.recordBlockedOk")}
          </button>
        </div>
      </div>
    </div>
  );
}
