import { useEffect, useState } from "react";
import { AlertTriangle, Layers, Play, RotateCcw, Square, Timer, Trash2, X } from "lucide-react";
import { useT } from "../../i18n/useT.js";

/**
 * @param {{
 *   queueLength: number,
 *   totalEstimateSec: number,
 *   batchRecording: boolean,
 *   onStart: () => void,
 *   onAbort: () => void,
 *   abortRequested?: boolean,
 *   onClear: () => void,
 *   undoCount?: number,
 *   onUndoClear?: () => void,
 *   onDismissUndo?: () => void,
 *   disabledStart: boolean,
 *   obsConfigured: boolean,
 * }} props
 */
export default function RecordingControlDock({
  queueLength,
  totalEstimateSec,
  batchRecording,
  onStart,
  onAbort,
  abortRequested = false,
  onClear,
  undoCount = 0,
  onUndoClear,
  onDismissUndo,
  disabledStart,
  obsConfigured,
}) {
  const t = useT();
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false);

  useEffect(() => {
    if (queueLength === 0 || batchRecording) setClearConfirmOpen(false);
  }, [queueLength, batchRecording]);

  const estLabel =
    totalEstimateSec <= 0
      ? "—"
      : totalEstimateSec >= 3600
        ? `${Math.floor(totalEstimateSec / 3600)}h ${Math.round((totalEstimateSec % 3600) / 60)}m`
        : `${Math.max(1, Math.round(totalEstimateSec / 60))} min`;

  const statusLabel = batchRecording
    ? t("queue.dockStatusRecording")
    : queueLength
      ? t("queue.dockStatusReady")
      : t("queue.dockStatusIdle");
  const startDisabled = disabledStart;

  const confirmClear = () => {
    setClearConfirmOpen(false);
    onClear();
  };

  return (
    <div className="relative shrink-0 border-t border-cs2-border bg-cs2-bg-page/95 backdrop-blur-md">
      {undoCount > 0 ? (
        <div
          role="status"
          className="mx-4 mt-3 flex items-center gap-3 rounded-md border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-[12px] text-cs2-text-secondary sm:mx-5"
        >
          <RotateCcw className="h-4 w-4 shrink-0 text-amber-300" />
          <span className="min-w-0 flex-1">{t("queue.clearUndoMessage", { n: undoCount })}</span>
          <button
            type="button"
            onClick={onUndoClear}
            className="shrink-0 rounded border border-amber-300/35 px-2.5 py-1 font-bold text-amber-200 transition-colors hover:bg-amber-300/10"
          >
            {t("common.undo")}
          </button>
          <button
            type="button"
            onClick={onDismissUndo}
            className="rounded p-1 text-cs2-text-muted transition-colors hover:bg-cs2-surface-2 hover:text-cs2-text-primary"
            aria-label={t("common.close")}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-4 px-4 py-3 sm:gap-4 sm:px-5">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11px] text-cs2-text-muted">
        <span className="inline-flex items-center gap-1">
          <Layers className="h-3 w-3 text-cs2-text-muted" />
          <span className="text-cs2-text-muted">{t("queue.dockTasks")}</span>
          <span className="tabular-nums text-cs2-text-secondary">{queueLength}</span>
        </span>
        <span className="inline-flex items-center gap-1">
          <Timer className="h-3 w-3 text-cs2-text-muted" />
          <span className="text-cs2-text-muted">{t("queue.dockEst")}</span>
          <span className="tabular-nums text-cs2-text-secondary">{estLabel}</span>
        </span>
        <span className="inline-flex items-center gap-1">
          <span
            className={`h-1.5 w-1.5 rounded-full ${batchRecording ? "animate-pulse bg-emerald-400" : "bg-zinc-600"}`}
          />
          {statusLabel}
        </span>
        </div>

        <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
        <div className="flex flex-col items-end gap-0.5">
          <button
            type="button"
            disabled={startDisabled}
            onClick={() => onStart()}
            className="inline-flex items-center gap-1.5 rounded-md bg-cs2-accent px-3 py-2 text-[12px] font-bold text-cs2-text-on-accent shadow-sm shadow-cs2-accent/20 transition-colors hover:bg-cs2-accent-light disabled:cursor-not-allowed disabled:opacity-35"
          >
            <Play className="h-3.5 w-3.5" />
            {t("queue.btnStartRecording")}
          </button>
        </div>
        <button
          type="button"
          disabled={!batchRecording || abortRequested}
          onClick={() => void onAbort()}
          className="inline-flex items-center gap-1 rounded-md border border-cs2-border px-2.5 py-2 text-[12px] font-semibold text-cs2-text-secondary transition-colors hover:border-red-500/40 hover:text-cs2-red-on-surface disabled:cursor-not-allowed disabled:opacity-30"
        >
          <Square className="h-3.5 w-3.5" />
          {t("queue.btnStop")}
        </button>
        <button
          type="button"
          disabled={queueLength === 0 || batchRecording}
          onClick={() => setClearConfirmOpen(true)}
          className="inline-flex items-center gap-1 rounded-md border border-red-500/25 px-2.5 py-2 text-[12px] font-semibold text-cs2-text-muted transition-colors hover:border-red-500/45 hover:bg-red-500/5 hover:text-cs2-red-on-surface disabled:opacity-30"
        >
          <Trash2 className="h-3.5 w-3.5" />
          {t("queue.clearAllBtn", { n: queueLength })}
        </button>
        </div>
      </div>

      {clearConfirmOpen ? (
        <div
          className="fixed inset-0 z-[120] flex items-center justify-center bg-black/65 p-4 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-labelledby="recording-queue-clear-title"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setClearConfirmOpen(false);
          }}
        >
          <div className="w-full max-w-md rounded-xl border border-red-500/25 bg-cs2-bg-card p-5 shadow-2xl shadow-black/50">
            <div className="flex items-start gap-3">
              <span className="rounded-lg bg-red-500/10 p-2 text-red-300">
                <AlertTriangle className="h-5 w-5" />
              </span>
              <div className="min-w-0">
                <h2 id="recording-queue-clear-title" className="text-[15px] font-bold text-cs2-text-primary">
                  {t("queue.clearConfirmTitle")}
                </h2>
                <p className="mt-1.5 text-[12px] leading-relaxed text-cs2-text-secondary">
                  {t("queue.clearConfirmBody", { n: queueLength })}
                </p>
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setClearConfirmOpen(false)}
                className="rounded-md border border-cs2-border px-3 py-2 text-[12px] font-semibold text-cs2-text-secondary transition-colors hover:bg-cs2-surface-2"
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                onClick={confirmClear}
                className="rounded-md bg-red-500 px-3 py-2 text-[12px] font-bold text-white transition-colors hover:bg-red-400"
              >
                {t("queue.clearConfirmAction")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
