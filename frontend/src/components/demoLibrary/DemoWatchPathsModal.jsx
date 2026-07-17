import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, FolderSearch, Loader2, Plus, Trash2 } from "lucide-react";
import { useT } from "../../i18n/useT.js";

export default function DemoWatchPathsModal({
  open,
  onClose,
  demoWatchPaths = [],
  onDemoWatchPathsChange,
  onSaveConfig,
}) {
  const t = useT();
  const [watchPathInput, setWatchPathInput] = useState("");
  const [browsing, setBrowsing] = useState(false);
  const [saveState, setSaveState] = useState({ status: "idle", message: "" });

  useEffect(() => {
    if (!open) return;
    setWatchPathInput("");
    setBrowsing(false);
    setSaveState({ status: "idle", message: "" });
  }, [open]);

  if (!open) return null;

  const persistPaths = async (next) => {
    setSaveState({ status: "saving", message: t("library.watchPathsSaving") });
    try {
      const result = await onSaveConfig?.({ demo_watch_paths: next });
      if (result?.ok === false) {
        throw new Error(result.error || t("library.watchPathsSaveFallbackError"));
      }
      onDemoWatchPathsChange?.(next);
      setSaveState({ status: "saved", message: t("library.watchPathsSaved") });
      return true;
    } catch (error) {
      setSaveState({
        status: "error",
        message: t("library.watchPathsSaveFail", {
          msg: error?.message || t("library.watchPathsSaveFallbackError"),
        }),
      });
      return false;
    }
  };

  const addPath = async () => {
    const path = watchPathInput.trim();
    if (!path || saveState.status === "saving") return;
    const duplicate = (demoWatchPaths || []).some(
      (existing) => String(existing).toLocaleLowerCase() === path.toLocaleLowerCase(),
    );
    if (duplicate) {
      setSaveState({ status: "error", message: t("library.watchPathsDuplicate") });
      return;
    }
    const next = [...(demoWatchPaths || []), path];
    if (await persistPaths(next)) setWatchPathInput("");
  };

  const removePath = async (path) => {
    if (saveState.status === "saving") return;
    const next = (demoWatchPaths || []).filter((item) => item !== path);
    await persistPaths(next);
  };

  const browseDirectory = async () => {
    if (browsing || saveState.status === "saving") return;
    if (!window.electron?.showOpenDialog) {
      setSaveState({ status: "error", message: t("library.watchPathsBrowseUnavailable") });
      return;
    }
    setBrowsing(true);
    setSaveState({ status: "idle", message: "" });
    try {
      const result = await window.electron.showOpenDialog({
        title: t("library.watchPathsBrowseTitle"),
        defaultPath: watchPathInput.trim() || demoWatchPaths[0] || undefined,
        properties: ["openDirectory"],
      });
      if (!result?.canceled && result?.filePaths?.[0]) {
        setWatchPathInput(result.filePaths[0]);
      }
    } catch (error) {
      setSaveState({
        status: "error",
        message: t("library.watchPathsBrowseFail", {
          msg: error?.message || t("library.watchPathsBrowseUnavailable"),
        }),
      });
    } finally {
      setBrowsing(false);
    }
  };

  const saving = saveState.status === "saving";

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-cs2-bg-page/85 px-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="demo-watch-paths-title"
      onClick={saving ? undefined : onClose}
    >
      <div
        className="w-full max-w-lg overflow-hidden rounded-xl border border-cs2-border bg-cs2-bg-card shadow-2xl shadow-black/55"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="border-b border-cs2-border bg-cs2-bg-input/25 px-5 py-4">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-cs2-accent/25 bg-cs2-accent/10">
              <FolderSearch className="h-4 w-4 text-cs2-accent" />
            </div>
            <div>
              <h4 id="demo-watch-paths-title" className="text-xs font-bold text-cs2-text-primary">
                {t("library.watchPathsTitle")}
              </h4>
              <p className="mt-0.5 text-[9px] font-semibold uppercase tracking-[0.16em] text-cs2-text-muted">
                {t("library.watchPathsModeLabel")}
              </p>
            </div>
          </div>
          <p className="mt-3 text-[11px] leading-relaxed text-cs2-text-secondary">
            {t("library.watchPathsHint")}
          </p>
        </div>

        <div className="p-5">
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              type="text"
              value={watchPathInput}
              onChange={(event) => {
                setWatchPathInput(event.target.value);
                if (saveState.status !== "saving") setSaveState({ status: "idle", message: "" });
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  void addPath();
                }
              }}
              placeholder="D:\\Demos\\CS2"
              disabled={saving}
              aria-label={t("library.watchPathsInputLabel")}
              className="min-w-0 flex-1 rounded-md border border-cs2-border bg-cs2-bg-input px-3 py-2 font-mono text-xs text-cs2-text-primary transition-colors placeholder:text-cs2-text-secondary/50 focus:border-cs2-accent/50 focus:outline-none disabled:opacity-50"
            />
            <button
              type="button"
              className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md border border-cs2-border bg-cs2-bg-input px-3 py-2 text-xs font-semibold text-cs2-text-secondary transition-colors hover:border-cs2-accent/50 hover:text-cs2-text-primary disabled:opacity-45"
              onClick={() => void browseDirectory()}
              disabled={saving || browsing}
            >
              {browsing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FolderSearch className="h-3.5 w-3.5" />}
              {browsing ? t("library.watchPathsBrowsing") : t("library.watchPathsBrowse")}
            </button>
            <button
              type="button"
              className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md border border-cs2-accent/40 bg-cs2-accent/10 px-3 py-2 text-xs font-bold text-cs2-accent transition-colors hover:bg-cs2-accent/20 disabled:opacity-45"
              onClick={() => void addPath()}
              disabled={saving || !watchPathInput.trim()}
            >
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
              {t("library.watchPathsAdd")}
            </button>
          </div>

          <div className="mt-4 max-h-52 space-y-1.5 overflow-y-auto custom-scrollbar">
            {(demoWatchPaths || []).length === 0 ? (
              <div className="rounded-lg border border-dashed border-cs2-border bg-cs2-bg-input/25 py-6 text-center">
                <p className="text-[11px] text-cs2-text-muted">{t("library.watchPathsEmpty")}</p>
              </div>
            ) : (
              (demoWatchPaths || []).map((path, index) => (
                <div
                  key={path}
                  className="group flex items-center justify-between gap-3 rounded-lg border border-cs2-border bg-cs2-bg-input/55 px-3 py-2 transition-colors hover:border-cs2-accent/25"
                >
                  <div className="min-w-0">
                    <p className="text-[9px] font-bold uppercase tracking-[0.14em] text-cs2-accent/75">
                      {t("library.watchPathsRootLabel", { index: String(index + 1).padStart(2, "0") })}
                    </p>
                    <p className="truncate font-mono text-[11px] text-cs2-text-secondary" title={path}>{path}</p>
                  </div>
                  <button
                    type="button"
                    className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-cs2-text-muted transition-colors hover:bg-red-500/10 hover:text-cs2-fail disabled:opacity-40"
                    onClick={() => void removePath(path)}
                    disabled={saving}
                    aria-label={t("library.watchPathsRemovePath", { path })}
                    title={t("library.watchPathsRemove")}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))
            )}
          </div>

          {saveState.message ? (
            <div
              className={`mt-3 flex items-start gap-2 rounded-md border px-3 py-2 text-[11px] ${
                saveState.status === "error"
                  ? "border-red-500/30 bg-red-500/5 text-cs2-text-error"
                  : saveState.status === "saved"
                    ? "border-emerald-500/25 bg-emerald-500/5 text-cs2-emerald-on-surface"
                    : "border-cs2-accent/25 bg-cs2-accent/5 text-cs2-text-secondary"
              }`}
              role={saveState.status === "error" ? "alert" : "status"}
            >
              {saveState.status === "saving" ? (
                <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-cs2-accent" />
              ) : saveState.status === "saved" ? (
                <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              ) : (
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              )}
              <span>{saveState.message}</span>
            </div>
          ) : null}
        </div>

        <div className="flex items-center justify-between border-t border-cs2-border bg-cs2-bg-page px-5 py-3">
          <p className="text-[10px] text-cs2-text-muted">{t("library.watchPathsManualScanNote")}</p>
          <button
            type="button"
            className="rounded-md border border-cs2-border px-3 py-1.5 text-[12px] text-cs2-text-secondary hover:border-cs2-accent/35 hover:text-cs2-text-primary disabled:opacity-45"
            onClick={onClose}
            disabled={saving}
          >
            {t("library.watchPathsClose")}
          </button>
        </div>
      </div>
    </div>
  );
}
