import { useEffect, useState } from "react";
import { FolderOpen, Layers3, Plus, Trash2 } from "lucide-react";
import { desktopBridge } from "../../desktop/desktopBridge.js";
import { useT } from "../../i18n/useT.js";

export default function DemoWatchPathsModal({
  open,
  onClose,
  demoWatchPaths = [],
  demoScanDepth = 2,
  onDemoWatchPathsChange,
  onDemoScanDepthChange,
  onSaveConfig,
}) {
  const t = useT();
  const [watchPathInput, setWatchPathInput] = useState("");

  useEffect(() => {
    if (open) setWatchPathInput("");
  }, [open]);

  if (!open) return null;

  const addPath = () => {
    const p = watchPathInput.trim();
    if (!p) return;
    const next = Array.from(new Set([...(demoWatchPaths || []), p]));
    onDemoWatchPathsChange?.(next);
    onSaveConfig?.({ demo_watch_paths: next });
    setWatchPathInput("");
  };

  const removePath = (p) => {
    const next = (demoWatchPaths || []).filter((x) => x !== p);
    onDemoWatchPathsChange?.(next);
    onSaveConfig?.({ demo_watch_paths: next });
  };

  const chooseDirectories = async () => {
    if (!desktopBridge) return;
    try {
      const result = await desktopBridge.showOpenDialog({
        title: t("library.watchPathsChooseTitle"),
        properties: ["openDirectory", "multiSelections"],
      });
      const selected = (result?.filePaths || []).map((path) => String(path).trim()).filter(Boolean);
      if (!selected.length) return;
      const next = Array.from(new Set([...(demoWatchPaths || []), ...selected]));
      onDemoWatchPathsChange?.(next);
      onSaveConfig?.({ demo_watch_paths: next });
    } catch (error) {
      console.error("Choose demo watch directories failed", error);
    }
  };

  const updateDepth = (value) => {
    const depth = Number.parseInt(value, 10);
    onDemoScanDepthChange?.(depth);
    onSaveConfig?.({ demo_scan_depth: depth });
  };

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-cs2-bg-page/85 px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="demo-watch-paths-title"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-lg border border-cs2-border bg-cs2-bg-card p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h4 id="demo-watch-paths-title" className="mb-1 text-xs font-semibold text-cs2-text-secondary">
          {t("library.watchPathsTitle")}
        </h4>
        <p className="mb-3 text-[11px] leading-relaxed text-cs2-text-secondary">
          {t("library.watchPathsHint", { csgo: "csgo", gamecsgo: "game/csgo" })}
        </p>
        <button
          type="button"
          disabled={!desktopBridge}
          className="mb-3 inline-flex w-full items-center justify-center gap-2 rounded-md border border-cs2-accent/45 bg-cs2-accent/10 px-3 py-2.5 text-xs font-semibold text-cs2-accent transition-colors hover:bg-cs2-accent/20 disabled:cursor-not-allowed disabled:opacity-45"
          onClick={() => void chooseDirectories()}
        >
          <FolderOpen className="h-4 w-4" aria-hidden />
          {t("library.watchPathsChoose")}
        </button>

        <div className="mb-2 flex items-center gap-2 text-[11px] font-medium text-cs2-text-muted">
          <span className="h-px flex-1 bg-cs2-border" />
          {t("library.watchPathsManual")}
          <span className="h-px flex-1 bg-cs2-border" />
        </div>
        <div className="mb-3 flex gap-2">
          <input
            type="text"
            value={watchPathInput}
            onChange={(e) => setWatchPathInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addPath();
              }
            }}
            placeholder="D:\\SteamLibrary\\...\\csgo"
            className="w-full rounded-md border border-cs2-border bg-cs2-bg-input px-3 py-2 font-mono text-xs text-cs2-text-primary transition-colors placeholder:text-cs2-text-secondary/50 focus:border-cs2-accent/50 focus:outline-none"
          />
          <button
            type="button"
            className="inline-flex shrink-0 items-center gap-1 rounded-md border border-cs2-border bg-cs2-bg-input px-3 text-xs font-semibold hover:border-cs2-accent/50"
            onClick={addPath}
          >
            <Plus className="h-3.5 w-3.5" aria-hidden />
            {t("library.watchPathsAdd")}
          </button>
        </div>
        <div className="mb-3 max-h-40 space-y-1 overflow-y-auto">
          {(demoWatchPaths || []).length === 0 ? (
            <p className="py-2 text-center text-[11px] text-cs2-text-muted">{t("library.watchPathsEmpty")}</p>
          ) : (
            (demoWatchPaths || []).map((p) => (
              <div
                key={p}
                className="flex items-center justify-between gap-2 rounded border border-cs2-border bg-cs2-bg-input/60 px-2 py-1"
              >
                <span className="min-w-0 truncate font-mono text-[11px] text-cs2-text-secondary">{p}</span>
                <button
                  type="button"
                  className="inline-flex shrink-0 items-center gap-1 text-[11px] text-cs2-fail hover:opacity-80"
                  onClick={() => removePath(p)}
                >
                  <Trash2 className="h-3 w-3" aria-hidden />
                  {t("library.watchPathsRemove")}
                </button>
              </div>
            ))
          )}
        </div>
        <div className="mb-4 rounded-md border border-cs2-border bg-cs2-bg-input/50 p-3">
          <label className="flex items-center justify-between gap-3">
            <span className="min-w-0">
              <span className="flex items-center gap-1.5 text-xs font-semibold text-cs2-text-primary">
                <Layers3 className="h-3.5 w-3.5 text-cs2-accent" aria-hidden />
                {t("library.watchPathsDepth")}
              </span>
              <span className="mt-1 block text-[11px] leading-relaxed text-cs2-text-muted">
                {t("library.watchPathsDepthHint")}
              </span>
            </span>
            <select
              value={demoScanDepth}
              onChange={(event) => updateDepth(event.target.value)}
              className="shrink-0 rounded-md border border-cs2-border bg-cs2-bg-input px-2 py-1.5 text-xs text-cs2-text-primary outline-none focus:border-cs2-accent/50"
            >
              <option value={0}>{t("library.watchPathsDepth0")}</option>
              <option value={1}>{t("library.watchPathsDepth1")}</option>
              <option value={2}>{t("library.watchPathsDepth2")}</option>
              <option value={3}>{t("library.watchPathsDepth3")}</option>
              <option value={5}>{t("library.watchPathsDepth5")}</option>
              <option value={-1}>{t("library.watchPathsDepthAll")}</option>
            </select>
          </label>
        </div>
        <div className="flex justify-end">
          <button
            type="button"
            className="rounded border border-cs2-border px-3 py-1.5 text-[12px] text-cs2-text-secondary hover:border-cs2-accent/35 hover:text-cs2-text-primary"
            onClick={onClose}
          >
            {t("library.watchPathsClose")}
          </button>
        </div>
      </div>
    </div>
  );
}
