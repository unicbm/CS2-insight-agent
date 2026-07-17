import React, { useCallback, useEffect, useRef, useState } from "react";
import API from "../api/api";
import { useT } from "../i18n/useT.js";
import {
  AlertTriangle,
  CheckCheck,
  ChevronLeft,
  ChevronRight,
  Database,
  HardDrive,
  Loader2,
  Plus,
  RefreshCcw,
  Search,
  Upload,
  X,
} from "lucide-react";

const PAGE_LIMIT = 10;
const SELECT_ALL_PAGE_LIMIT = 1000;
export const INGEST_CHUNK_SIZE = 4;

// Source labels that should NOT be translated (proper names / abbreviations)
const SOURCE_LABELS_FIXED = new Set(["Faceit", "5E", "ESL", "ESEA", "Blast"]);
// i18n keys for platform display labels
const SOURCE_I18N_KEYS = {
  "Perfect World": "ingest.sourcePerfectWorld",
  "Matchmaking": "ingest.sourceMatchmaking",
};

function apiErrorMessage(error, fallback) {
  const detail = error?.response?.data?.detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (typeof item === "object" && item?.msg ? item.msg : String(item)))
      .join("；");
  }
  if (typeof detail === "string") return detail;
  return error?.message || fallback;
}

export default function IngestModal({
  isOpen,
  onClose,
  onIngest,
  onIngestComplete,
  onUpload,
}) {
  const t = useT();
  const [items, setItems] = useState([]);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState("");
  const [ingesting, setIngesting] = useState(false);
  const [ingestError, setIngestError] = useState(null);
  const [ingestFailures, setIngestFailures] = useState([]);
  const [ingestProgress, setIngestProgress] = useState(null);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [selectingAll, setSelectingAll] = useState(false);
  const [selectionError, setSelectionError] = useState("");

  const fileInputRef = useRef(null);
  const listRequestIdRef = useRef(0);
  const selectAllRequestIdRef = useRef(0);

  const handleFileChange = (event) => {
    const files = Array.from(event.target.files || []);
    if (files.length > 0) {
      onUpload?.(files);
      event.target.value = "";
    }
  };

  const fetchDiscovered = useCallback(
    async ({ pageOverride = page, searchOverride = debouncedSearch } = {}) => {
      const requestId = ++listRequestIdRef.current;
      setListLoading(true);
      setListError("");
      try {
        const params = { limit: PAGE_LIMIT, offset: (pageOverride - 1) * PAGE_LIMIT };
        if (searchOverride) params.q = searchOverride;

        const { data } = await API.get("/demos/discovered", { params });
        if (requestId !== listRequestIdRef.current) return;
        setItems(data.items || []);
        setTotal(typeof data.total === "number" ? data.total : 0);
      } catch (error) {
        if (requestId !== listRequestIdRef.current) return;
        setItems([]);
        setTotal(0);
        setListError(apiErrorMessage(error, t("dialog.ingestListFallbackError")));
      } finally {
        if (requestId === listRequestIdRef.current) setListLoading(false);
      }
    },
    [debouncedSearch, page, t],
  );

  useEffect(() => {
    if (!isOpen) {
      listRequestIdRef.current += 1;
      selectAllRequestIdRef.current += 1;
      return;
    }
    setItems([]);
    setListError("");
    setIngestError(null);
    setIngestFailures([]);
    setIngestProgress(null);
    setIngesting(false);
    setSelectingAll(false);
    setSelectionError("");
    setSearch("");
    setDebouncedSearch("");
    setPage(1);
    setTotal(0);
    setSelectedIds(new Set());
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return undefined;
    const timer = window.setTimeout(() => {
      setPage(1);
      setDebouncedSearch(search.trim());
    }, 300);
    return () => window.clearTimeout(timer);
  }, [isOpen, search]);

  useEffect(() => {
    if (isOpen) void fetchDiscovered();
  }, [fetchDiscovered, isOpen]);

  if (!isOpen) return null;

  const totalPages = Math.ceil(total / PAGE_LIMIT) || 1;
  const pageSelectedCount = items.reduce(
    (count, item) => count + (selectedIds.has(item.id) ? 1 : 0),
    0,
  );
  const searchSettling = search.trim() !== debouncedSearch;
  const progressPercent = ingestProgress?.total
    ? Math.round((ingestProgress.processed / ingestProgress.total) * 100)
    : 0;

  const handleToggleSelect = (id) => {
    if (ingesting || selectingAll || searchSettling) return;
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSelectPage = () => {
    if (ingesting || selectingAll || searchSettling) return;
    setSelectedIds((previous) => {
      const next = new Set(previous);
      for (const item of items) next.add(item.id);
      return next;
    });
  };

  const handleSelectAllFiltered = async () => {
    if (selectingAll || ingesting || total <= 0 || searchSettling) return;
    const requestId = ++selectAllRequestIdRef.current;
    const query = debouncedSearch;
    setSelectingAll(true);
    setSelectionError("");
    try {
      const ids = [];
      let offset = 0;
      let expectedTotal = total;
      while (offset < expectedTotal) {
        const params = { limit: SELECT_ALL_PAGE_LIMIT, offset };
        if (query) params.q = query;
        const { data } = await API.get("/demos/discovered", { params });
        if (requestId !== selectAllRequestIdRef.current) return;
        const rows = Array.isArray(data.items) ? data.items : [];
        expectedTotal = typeof data.total === "number" ? data.total : expectedTotal;
        ids.push(...rows.map((item) => item.id));
        if (rows.length === 0) break;
        offset += rows.length;
      }
      if (requestId !== selectAllRequestIdRef.current) return;
      setSelectedIds(new Set(ids));
      setTotal(expectedTotal);
    } catch (error) {
      if (requestId !== selectAllRequestIdRef.current) return;
      setSelectionError(apiErrorMessage(error, t("dialog.ingestSelectAllError")));
    } finally {
      if (requestId === selectAllRequestIdRef.current) setSelectingAll(false);
    }
  };

  const handleClearSelection = () => {
    selectAllRequestIdRef.current += 1;
    setSelectingAll(false);
    setSelectedIds(new Set());
    setSelectionError("");
  };

  const handleIngestSelected = async () => {
    if (selectedIds.size === 0 || ingesting || selectingAll || searchSettling || !onIngest) return;
    const ids = Array.from(selectedIds);
    let processed = 0;
    let succeeded = 0;
    const failures = [];

    setIngestError(null);
    setIngestFailures([]);
    setIngesting(true);
    setIngestProgress({ processed: 0, total: ids.length, succeeded: 0, failed: 0 });

    try {
      for (let start = 0; start < ids.length; start += INGEST_CHUNK_SIZE) {
        const chunk = ids.slice(start, start + INGEST_CHUNK_SIZE);
        try {
          const result = (await onIngest(chunk)) || {};
          const chunkFailures = Array.isArray(result.failed) ? result.failed : [];
          const fallbackSucceeded = Math.max(0, chunk.length - chunkFailures.length);
          const reportedSucceeded = Number(result.ingested);
          succeeded += Number.isFinite(reportedSucceeded)
            ? Math.max(0, Math.min(chunk.length, reportedSucceeded))
            : fallbackSucceeded;

          chunkFailures.forEach((failure, index) => {
            const candidateId = Number(failure?.demo_id);
            failures.push({
              demoId: Number.isFinite(candidateId) ? candidateId : chunk[index],
              filename: failure?.filename || "",
              error: failure?.error || t("dialog.ingestFailureFallback"),
            });
          });
        } catch (error) {
          const message = apiErrorMessage(error, t("dialog.ingestFallbackError"));
          chunk.forEach((demoId) => {
            failures.push({ demoId, filename: "", error: message });
          });
        }

        processed += chunk.length;
        setIngestProgress({
          processed,
          total: ids.length,
          succeeded,
          failed: failures.length,
        });
      }

      try {
        await onIngestComplete?.({ processed, total: ids.length, succeeded, failed: failures });
      } catch (error) {
        console.error("Failed to refresh demo library after ingest", error);
      }

      if (failures.length > 0) {
        // A failed ID may already have left the pending list (for example a
        // concurrent ingest changed its status).  Never retain invisible IDs:
        // keep the failure details visible and let the refreshed pending list
        // define which rows can safely be retried.
        setSelectedIds(new Set());
        setIngestFailures(failures);
        setPage(1);
        await fetchDiscovered({ pageOverride: 1 });
      } else {
        setSelectedIds(new Set());
        onClose();
      }
    } catch (error) {
      setIngestError(apiErrorMessage(error, t("dialog.ingestFallbackError")));
    } finally {
      setIngesting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-cs2-bg-overlay px-4 py-6 backdrop-blur-sm">
      <div className="relative flex h-full max-h-[640px] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-cs2-border bg-cs2-bg-card shadow-2xl">
        {ingesting ? (
          <div
            className="absolute inset-0 z-[2] flex items-center justify-center bg-black/70 px-5 backdrop-blur-[2px]"
            aria-busy="true"
            aria-label={t("dialog.ingestIngesting")}
          >
            <div className="w-full max-w-sm rounded-xl border border-cs2-accent/35 bg-cs2-bg-card/95 p-4 shadow-2xl shadow-black/60">
              <div className="flex items-center gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-cs2-accent/25 bg-cs2-accent/10">
                  <Loader2 className="h-5 w-5 animate-spin text-cs2-accent" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-cs2-accent">
                    {t("dialog.ingestProgressStage")}
                  </p>
                  <p className="mt-0.5 text-xs font-semibold text-cs2-text-primary">
                    {t("dialog.ingestProgressProcessed", {
                      done: ingestProgress?.processed || 0,
                      total: ingestProgress?.total || 0,
                    })}
                  </p>
                </div>
                <span className="font-mono text-sm font-bold text-cs2-text-primary">{progressPercent}%</span>
              </div>

              <div
                className="mt-4 h-1.5 overflow-hidden rounded-full bg-cs2-bg-input"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={ingestProgress?.total || 0}
                aria-valuenow={ingestProgress?.processed || 0}
              >
                <div
                  className="h-full rounded-full bg-gradient-to-r from-cs2-accent to-cs2-accent-light transition-[width] duration-300"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>

              <div className="mt-4 grid grid-cols-3 gap-2">
                <div className="rounded-md border border-cs2-border bg-cs2-bg-input/55 px-2 py-2 text-center">
                  <p className="text-[9px] uppercase tracking-wider text-cs2-text-muted">{t("dialog.ingestMetricProcessed")}</p>
                  <p className="mt-0.5 font-mono text-sm font-bold text-cs2-text-primary">{ingestProgress?.processed || 0}</p>
                </div>
                <div className="rounded-md border border-emerald-500/25 bg-emerald-500/5 px-2 py-2 text-center">
                  <p className="text-[9px] uppercase tracking-wider text-cs2-text-muted">{t("dialog.ingestMetricSucceeded")}</p>
                  <p className="mt-0.5 font-mono text-sm font-bold text-cs2-emerald-on-surface">{ingestProgress?.succeeded || 0}</p>
                </div>
                <div className="rounded-md border border-red-500/25 bg-red-500/5 px-2 py-2 text-center">
                  <p className="text-[9px] uppercase tracking-wider text-cs2-text-muted">{t("dialog.ingestMetricFailed")}</p>
                  <p className="mt-0.5 font-mono text-sm font-bold text-cs2-red-on-surface">{ingestProgress?.failed || 0}</p>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        <div className="flex items-center justify-between border-b border-cs2-border px-5 py-4">
          <div className="flex items-center gap-2">
            <Database className="h-5 w-5 text-cs2-accent" />
            <h2 className="text-sm font-bold text-cs2-text-primary">{t("dialog.ingestTitle")}</h2>
            <span className="rounded bg-cs2-accent/10 px-1.5 py-0.5 text-[10px] font-bold text-cs2-accent">
              {total}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <input
              type="file"
              multiple
              accept=".dem"
              className="hidden"
              ref={fileInputRef}
              onChange={handleFileChange}
            />
            <button
              type="button"
              disabled={ingesting}
              onClick={() => fileInputRef.current?.click()}
              className="flex items-center gap-1.5 rounded-lg border border-cs2-border bg-cs2-bg-hover px-3 py-1.5 text-[10px] font-bold text-cs2-text-secondary transition-all hover:bg-cs2-bg-active hover:text-cs2-text-primary disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Upload className="h-3.5 w-3.5" />
              {t("dialog.ingestUploadBtn")}
            </button>
            <button
              type="button"
              disabled={ingesting}
              onClick={onClose}
              className="rounded-full p-1.5 text-cs2-text-muted hover:bg-cs2-bg-hover hover:text-cs2-text-primary disabled:cursor-not-allowed disabled:opacity-40"
              aria-label={t("dialog.ingestClose")}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="flex items-center gap-3 border-b border-cs2-border bg-cs2-bg-input/30 px-5 py-3">
          <div className="flex flex-1 items-center gap-2 rounded-md border border-cs2-border bg-cs2-bg-input px-2.5 py-1.5 focus-within:border-cs2-accent/45">
            <Search className="h-3.5 w-3.5 text-cs2-text-muted" />
            <input
              type="text"
              placeholder={t("dialog.ingestSearchPlaceholder")}
              aria-label={t("dialog.ingestSearchPlaceholder")}
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setSelectedIds(new Set());
                setSelectingAll(false);
                setSelectionError("");
                setIngestFailures([]);
                selectAllRequestIdRef.current += 1;
              }}
              className="flex-1 bg-transparent text-xs text-cs2-text-primary outline-none placeholder:text-cs2-text-muted"
            />
            {searchSettling ? <Loader2 className="h-3 w-3 animate-spin text-cs2-accent" aria-hidden /> : null}
          </div>
          <button
            type="button"
            disabled={ingesting || listLoading || selectingAll || searchSettling}
            onClick={() => void fetchDiscovered()}
            className="flex items-center justify-center rounded-md border border-cs2-border p-1.5 text-cs2-text-secondary hover:bg-cs2-bg-hover hover:text-cs2-text-primary disabled:cursor-not-allowed disabled:opacity-40"
            aria-label={t("dialog.ingestRefresh")}
          >
            <RefreshCcw className={`h-4 w-4 ${listLoading ? "animate-spin" : ""}`} />
          </button>
        </div>

        {(items.length > 0 || selectedIds.size > 0) && (
          <div className="flex flex-wrap items-center gap-2 border-b border-cs2-border bg-cs2-bg-input/30 px-5 py-2.5 text-[10px]">
            <button
              type="button"
              disabled={ingesting || listLoading || selectingAll || searchSettling || items.length === 0}
              onClick={handleSelectPage}
              className="rounded-md border border-cs2-border bg-cs2-bg-card px-2.5 py-1.5 font-semibold text-cs2-text-secondary transition-colors hover:border-cs2-accent/35 hover:text-cs2-text-primary disabled:opacity-40"
            >
              {t("dialog.ingestSelectPage", { selected: pageSelectedCount, total: items.length })}
            </button>
            <button
              type="button"
              disabled={ingesting || listLoading || selectingAll || total === 0 || searchSettling}
              onClick={() => void handleSelectAllFiltered()}
              className="inline-flex items-center gap-1.5 rounded-md border border-cs2-accent/40 bg-cs2-accent/10 px-2.5 py-1.5 font-bold text-cs2-accent transition-colors hover:bg-cs2-accent/20 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {selectingAll ? <Loader2 className="h-3 w-3 animate-spin" /> : <CheckCheck className="h-3 w-3" />}
              {selectingAll
                ? t("dialog.ingestSelectingAll")
                : t("dialog.ingestSelectAllFiltered", { total })}
            </button>
            <button
              type="button"
              disabled={ingesting || (!selectingAll && selectedIds.size === 0)}
              onClick={handleClearSelection}
              className="px-1.5 py-1 text-cs2-text-muted hover:text-cs2-text-primary disabled:opacity-40"
            >
              {t("dialog.ingestClear")}
            </button>
            <span className="ml-auto font-mono text-cs2-text-muted">
              {t("dialog.ingestSelectedFiltered", { selected: selectedIds.size, total })}
            </span>
          </div>
        )}

        {selectionError ? (
          <p className="border-b border-cs2-border bg-red-500/5 px-5 py-2 text-[11px] text-cs2-text-error" role="alert">
            {selectionError}
          </p>
        ) : null}

        <div className="flex-1 overflow-y-auto p-2">
          {searchSettling || listLoading ? (
            <div className="flex h-32 items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-cs2-text-muted" />
            </div>
          ) : listError ? (
            <div className="flex h-36 flex-col items-center justify-center gap-2 px-6 text-center" role="alert">
              <AlertTriangle className="h-7 w-7 text-cs2-text-error" />
              <p className="text-xs text-cs2-text-secondary">{listError}</p>
              <button
                type="button"
                onClick={() => void fetchDiscovered()}
                className="rounded-md border border-cs2-border px-3 py-1.5 text-[11px] font-semibold text-cs2-text-secondary hover:border-cs2-accent/40 hover:text-cs2-text-primary"
              >
                {t("dialog.ingestRetry")}
              </button>
            </div>
          ) : items.length === 0 ? (
            <div className="flex h-32 flex-col items-center justify-center gap-2 text-cs2-text-muted">
              <HardDrive className="h-8 w-8 opacity-20" />
              <p className="text-xs">
                {debouncedSearch ? t("dialog.ingestEmptySearch") : t("dialog.ingestEmptyNoDemo")}
              </p>
            </div>
          ) : (
            <div className="space-y-1">
              {items.map((item) => {
                const sourceLabel = SOURCE_LABELS_FIXED.has(item.source)
                  ? item.source
                  : SOURCE_I18N_KEYS[item.source]
                    ? t(SOURCE_I18N_KEYS[item.source])
                    : t("dialog.ingestSourceLocal");
                const sizeMB = item.file_size != null ? (item.file_size / (1024 * 1024)).toFixed(1) : "?";
                return (
                  <div
                    key={item.id}
                    role="button"
                    tabIndex={ingesting || selectingAll || searchSettling ? -1 : 0}
                    aria-disabled={ingesting || selectingAll || searchSettling}
                    onClick={() => handleToggleSelect(item.id)}
                    onKeyDown={(event) => {
                      if (ingesting || selectingAll || searchSettling) return;
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        handleToggleSelect(item.id);
                      }
                    }}
                    className={`flex items-center justify-between rounded-md border p-2.5 transition-colors ${ingesting || selectingAll || searchSettling ? "cursor-not-allowed opacity-60" : "cursor-pointer"} ${selectedIds.has(item.id) ? "border-cs2-accent/40 bg-cs2-accent/5" : "border-transparent hover:bg-cs2-bg-hover"}`}
                  >
                    <div className="flex min-w-0 items-center gap-3">
                      <input
                        type="checkbox"
                        readOnly
                        disabled={ingesting || selectingAll || searchSettling}
                        checked={selectedIds.has(item.id)}
                        aria-label={t("dialog.ingestSelectDemo", { filename: item.filename })}
                        className="h-3.5 w-3.5 rounded border-cs2-border bg-transparent text-cs2-accent focus:ring-offset-0"
                      />
                      <div className="min-w-0">
                        <p className="truncate text-xs font-mono text-cs2-text-secondary" title={item.path}>{item.filename}</p>
                        <div className="mt-0.5 flex items-center gap-2 text-[11px] text-cs2-text-muted">
                          <span>{sourceLabel}</span>
                          <span>•</span>
                          <span>{sizeMB} MB</span>
                          <span>•</span>
                          <span>{t("dialog.ingestDiscoveredAt", { date: new Date(item.added_at).toLocaleDateString() })}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-2 border-t border-cs2-border bg-cs2-bg-page px-5 py-3">
          {ingestFailures.length > 0 ? (
            <div className="max-h-28 overflow-y-auto rounded-md border border-red-500/30 bg-red-500/5 p-2.5" role="alert">
              <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold text-cs2-red-on-surface">
                <AlertTriangle className="h-3.5 w-3.5" />
                {t("dialog.ingestFailureTitle", { count: ingestFailures.length })}
              </div>
              <div className="space-y-1">
                {ingestFailures.map((failure, index) => (
                  <p key={`${failure.demoId}-${index}`} className="text-[10px] leading-snug text-cs2-text-secondary">
                    <span className="font-mono text-cs2-text-primary">{failure.filename || `#${failure.demoId}`}</span>
                    <span className="text-cs2-text-muted"> — {failure.error}</span>
                  </p>
                ))}
              </div>
            </div>
          ) : null}
          {ingestError ? (
            <p className="text-center text-[12px] leading-snug text-cs2-text-error" role="alert">{ingestError}</p>
          ) : null}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                disabled={page <= 1 || ingesting || listLoading || selectingAll || searchSettling}
                onClick={() => setPage((value) => value - 1)}
                className="rounded-md border border-cs2-border p-1 text-cs2-text-muted hover:bg-cs2-bg-hover hover:text-cs2-text-secondary disabled:opacity-30"
                aria-label={t("dialog.ingestPreviousPage")}
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <span className="text-[11px] text-cs2-text-muted">
                {t("dialog.ingestPageOf", { page, totalPages })}
              </span>
              <button
                type="button"
                disabled={page >= totalPages || ingesting || listLoading || selectingAll || searchSettling}
                onClick={() => setPage((value) => value + 1)}
                className="rounded-md border border-cs2-border p-1 text-cs2-text-muted hover:bg-cs2-bg-hover hover:text-cs2-text-secondary disabled:opacity-30"
                aria-label={t("dialog.ingestNextPage")}
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>

            <button
              type="button"
              disabled={selectedIds.size === 0 || ingesting || selectingAll || searchSettling}
              onClick={() => void handleIngestSelected()}
              className="flex items-center gap-1.5 rounded-lg bg-cs2-accent px-4 py-2 text-xs font-extrabold text-cs2-text-on-accent shadow-lg shadow-cs2-accent/20 transition-all hover:bg-cs2-accent-light disabled:opacity-50 disabled:grayscale"
            >
              {ingesting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
              {t("dialog.ingestConfirmBtn", { count: selectedIds.size })}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
