import { Database, FolderOpen, LayoutGrid, List, Loader2 } from "lucide-react";
import { useT } from "../../i18n/useT.js";

export default function DemoLibraryToolbar({
  onOpenWatchPaths,
  onScan,
  onOpenIngest,
  libraryLoading,
  libraryScanning,
  libraryScanStatus,
  pageSelectableCount,
  libraryTotal,
  onSelectPage,
  onSelectAllLibrary,
  viewMode = "table",
  onViewModeChange,
}) {
  const t = useT();
  const scanTotal = Number(libraryScanStatus?.total || 0);
  const scanProcessed = Number(libraryScanStatus?.processed || 0);
  const scanPercent = scanTotal > 0
    ? Math.max(0, Math.min(100, Math.round((scanProcessed / scanTotal) * 100)))
    : 0;
  const showScanStatus = libraryScanning || ["done", "error"].includes(libraryScanStatus?.state);

  const viewBtn = (mode, Icon) => (
    <button
      type="button"
      title={mode === "grid" ? t("library.viewGrid") : t("library.viewList")}
      onClick={() => onViewModeChange?.(mode)}
      className={`p-1.5 rounded-md transition-all ${viewMode === mode ? "bg-cs2-accent text-cs2-text-on-accent shadow" : "text-cs2-text-muted hover:text-cs2-text-secondary"}`}
    >
      <Icon className="h-3.5 w-3.5" />
    </button>
  );
  return (
    <div className="flex shrink-0 flex-col gap-2 border-b border-cs2-border pb-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-lg font-bold text-cs2-text-primary">{t("library.pageTitle")}</h1>
        <p className="mt-0.5 text-[12px] leading-relaxed text-cs2-text-muted">
          {t("library.pageSubtitle")}
        </p>
      </div>
      <div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
        <div className="mr-1 flex items-center rounded-lg bg-cs2-bg-input/70 p-1 border border-cs2-border">
          {viewBtn("grid", LayoutGrid)}
          {viewBtn("list", List)}
        </div>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 rounded-md border border-cs2-border bg-cs2-bg-hover px-2.5 py-1.5 text-[12px] font-semibold text-cs2-text-secondary hover:border-cs2-accent/35 hover:text-cs2-text-primary"
          onClick={() => onOpenWatchPaths?.()}
        >
          <FolderOpen className="h-3.5 w-3.5 shrink-0 text-cs2-accent/90" aria-hidden />
          {t("library.btnWatchPaths")}
        </button>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 rounded-md border border-cs2-accent/40 bg-cs2-accent/10 px-2.5 py-1.5 text-[12px] font-semibold text-cs2-accent hover:bg-cs2-accent/20"
          onClick={() => onOpenIngest?.()}
        >
          <Database className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {t("library.btnPending")}
        </button>
        <button
          type="button"
          disabled={libraryLoading || libraryScanning}
          className="inline-flex items-center gap-1.5 rounded-md border border-cs2-border bg-cs2-bg-hover px-2.5 py-1.5 text-[12px] font-semibold text-cs2-text-secondary hover:border-cs2-accent/35 hover:text-cs2-text-primary disabled:opacity-45"
          onClick={() => void onScan()}
        >
          {libraryScanning ? (
            <>
              <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-cs2-accent" aria-hidden />
              <span>{t("library.btnScanning")}</span>
            </>
          ) : (
            t("library.btnScan")
          )}
        </button>
        <button
          type="button"
          disabled={libraryLoading || pageSelectableCount === 0}
          className="rounded-md border border-cs2-border px-2.5 py-1.5 text-[12px] font-semibold text-cs2-text-secondary hover:border-cs2-accent/35 hover:text-cs2-text-primary disabled:cursor-not-allowed disabled:opacity-35"
          onClick={onSelectPage}
        >
          {t("library.btnSelectPage")}
        </button>
        <button
          type="button"
          disabled={libraryLoading || (libraryTotal != null && libraryTotal === 0)}
          className="rounded-md border border-cs2-border px-2.5 py-1.5 text-[12px] font-semibold text-cs2-text-secondary hover:border-cs2-accent/35 hover:text-cs2-text-primary disabled:cursor-not-allowed disabled:opacity-35"
          onClick={() => void onSelectAllLibrary()}
        >
          {t("library.btnSelectAll")}
        </button>
      </div>
      </div>
      {showScanStatus ? (
        <div
          className={`rounded-md border px-3 py-2 ${libraryScanStatus?.state === "error" ? "border-cs2-fail/35 bg-cs2-fail/5" : "border-cs2-accent/25 bg-cs2-accent/5"}`}
          role="status"
          aria-live="polite"
        >
          <div className="flex items-center justify-between gap-3 text-[11px]">
            <span className="min-w-0 truncate font-semibold text-cs2-text-secondary">
              {t(`library.scanPhase.${libraryScanStatus?.phase || "indexing"}`)}
              {libraryScanStatus?.current_file ? ` · ${libraryScanStatus.current_file}` : ""}
            </span>
            <span className="shrink-0 font-mono text-cs2-text-muted">
              {scanTotal > 0 ? `${scanProcessed}/${scanTotal} · ${scanPercent}%` : t("library.scanIndexing")}
            </span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-cs2-bg-input">
            <div
              className={`h-full rounded-full transition-[width] duration-300 ${libraryScanStatus?.state === "error" ? "bg-cs2-fail" : "bg-cs2-accent"}`}
              style={{ width: libraryScanStatus?.state === "done" ? "100%" : `${scanPercent}%` }}
            />
          </div>
          {libraryScanStatus?.skipped_existing > 0 ? (
            <p className="mt-1.5 text-[10px] text-cs2-text-muted">
              {t("library.scanSkippedExisting", { count: libraryScanStatus.skipped_existing })}
            </p>
          ) : null}
          {libraryScanStatus?.error ? (
            <p className="mt-1.5 text-[10px] text-cs2-fail">{libraryScanStatus.error}</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
