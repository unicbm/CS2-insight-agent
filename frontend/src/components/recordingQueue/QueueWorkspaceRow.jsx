import { GripVertical, Trash2 } from "lucide-react";
import { useT } from "../../i18n/useT.js";
import { estimateItemRecordSeconds } from "../../utils/recordingQueueDerive";
import { friendlyClipTitleForQueue, isTimelineSourceClip } from "../../utils/montageUtils";
import {
  freezeToDeathQueueRoundBadgeText,
  isFreezeToDeathCompilation,
} from "../../utils/freezeToDeathRoundFilter";

function categoryRailClass(timeline, category) {
  if (timeline) return "bg-cyan-400/80";
  if (category === "highlight") return "bg-cs2-highlight/80";
  if (category === "fail") return "bg-cs2-fail/80";
  if (category === "meme_death") return "bg-fuchsia-400/80";
  if (category === "compilation") return "bg-cs2-compilation/80";
  return "bg-cs2-text-muted/50";
}

function categoryBadgeClass(timeline, category) {
  const base = "shrink-0 rounded border px-1.5 py-px text-[9px] font-semibold";
  if (timeline) return `${base} border-cyan-500/25 text-cs2-cyan-on-surface`;
  if (category === "highlight") return `${base} border-cs2-highlight/25 text-cs2-highlight`;
  if (category === "fail") return `${base} border-cs2-fail/25 text-cs2-fail`;
  if (category === "meme_death") return `${base} border-fuchsia-500/25 text-cs2-fuchsia-on-surface`;
  if (category === "compilation") return `${base} border-cs2-compilation/25 text-cs2-compilation`;
  return `${base} border-cs2-border text-cs2-text-secondary`;
}

function compactFilename(value) {
  const filename = String(value || "").trim().split(/[\\/]/).pop() || "—";
  if (filename.length <= 38) return filename;
  const dot = filename.lastIndexOf(".");
  const suffix = dot > 0 ? filename.slice(dot) : "";
  return `${filename.slice(0, 28)}…${suffix}`;
}

/**
 * Compact queue row. Editing detail lives in the inspector; this list only
 * answers what the clip is, who it follows, where it came from, and its cost.
 */
export default function QueueWorkspaceRow({
  item,
  priorityIndex,
  selected,
  onSelect,
  onRemove,
  globalPacing,
  queueIndex = 0,
  dragReorderEnabled = false,
  onReorderDragStart,
  onReorderDragEnd,
}) {
  const t = useT();
  const clip = item.clipData || {};
  const category = clip.category || "";
  const timeline = isTimelineSourceClip(clip);
  const title = friendlyClipTitleForQueue(clip, t);
  const demoLabel = compactFilename(item.demoFilename || item.demoPath);
  const playerLabel = String(item.targetPlayer || "").trim() || "—";
  const mapName = String(clip.map_name || clip.map || "").trim() || "—";
  const freezeRound = freezeToDeathQueueRoundBadgeText(item, clip, t);
  const roundLabel = isFreezeToDeathCompilation(clip)
    ? freezeRound || "—"
    : clip.round != null
      ? `R${clip.round}`
      : "—";
  const kills = Number(clip.kill_count) || 0;
  const estimatedSeconds = estimateItemRecordSeconds(item, globalPacing);
  const categoryKey = timeline
    ? "queue.rowCatTimeline"
    : ({
        highlight: "queue.rowCatHighlight",
        fail: "queue.rowCatFail",
        meme_death: "queue.rowCatMemeDeath",
        compilation: "queue.rowCatCompilation",
      })[category] || "queue.rowCatDefault";

  return (
    <div
      className={[
        "group relative flex min-h-14 w-full items-stretch overflow-hidden rounded-md border text-left transition-colors",
        selected
          ? "border-cs2-accent/55 bg-cs2-accent/[0.07]"
          : "border-cs2-border bg-cs2-bg-input/25 hover:bg-cs2-bg-hover",
      ].join(" ")}
    >
      <span className={`w-0.5 shrink-0 ${categoryRailClass(timeline, category)}`} aria-hidden />

      {dragReorderEnabled ? (
        <div
          draggable
          onDragStart={(event) => {
            event.stopPropagation();
            event.dataTransfer.setData("text/plain", String(queueIndex));
            event.dataTransfer.effectAllowed = "move";
            onReorderDragStart?.();
          }}
          onDragEnd={() => onReorderDragEnd?.()}
          className="flex w-6 shrink-0 cursor-grab touch-none items-center justify-center text-cs2-text-muted active:cursor-grabbing hover:bg-cs2-bg-hover hover:text-cs2-text-secondary"
          title={t("queue.rowDragTitle")}
          aria-label={t("queue.rowDragAriaLabel")}
        >
          <GripVertical className="h-3.5 w-3.5" aria-hidden />
        </div>
      ) : null}

      <button
        type="button"
        onClick={onSelect}
        className="min-w-0 flex-1 px-2.5 py-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cs2-accent/45"
      >
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-cs2-text-muted">
            #{priorityIndex}
          </span>
          <span className="min-w-0 flex-1 truncate text-[12px] font-semibold text-cs2-text-primary" title={title}>
            {title}
          </span>
          <span className={categoryBadgeClass(timeline, category)}>{t(categoryKey)}</span>
          <span className="max-w-[28%] shrink-0 truncate text-[11px] font-semibold text-cs2-text-secondary" title={playerLabel}>
            {playerLabel}
          </span>
        </div>

        <div className="mt-1 flex min-w-0 items-center gap-2 font-mono text-[10px] text-cs2-text-muted">
          <span className="min-w-0 flex-1 truncate" title={String(item.demoFilename || item.demoPath || "")}>
            {demoLabel}
          </span>
          <span className="shrink-0">{mapName}</span>
          <span className="shrink-0">{roundLabel}</span>
          {kills > 0 ? <span className="shrink-0">{t("queue.rowKills", { n: kills })}</span> : null}
          <span className="shrink-0 tabular-nums">~{Math.max(0, Math.round(estimatedSeconds || 0))}s</span>
        </div>
      </button>

      <button
        type="button"
        onClick={onRemove}
        className="flex w-8 shrink-0 items-center justify-center text-cs2-text-muted opacity-55 transition-colors hover:bg-cs2-rose-surface hover:text-cs2-rose-on-surface group-hover:opacity-100 focus-visible:opacity-100"
        aria-label={t("queue.rowRemoveAriaLabel")}
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
