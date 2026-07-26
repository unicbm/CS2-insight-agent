import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import API, { API_BASE_URL } from "../api/api";
import type { Translate } from "../i18n/typedUseT";

export interface DemoLibraryFilters {
  mapName: string;
  status: string;
  playerQuery: string;
  steamQuery: string;
  minKills: string;
  maxDeaths: string;
  minAssists: string;
  minKd: string;
  roundsMin: string;
  roundsMax: string;
  durationMin: string;
  durationMax: string;
  dateFrom: string;
  dateTo: string;
}

export const EMPTY_DEMO_LIBRARY_FILTERS: DemoLibraryFilters = {
  mapName: "",
  status: "all",
  playerQuery: "",
  steamQuery: "",
  minKills: "",
  maxDeaths: "",
  minAssists: "",
  minKd: "",
  roundsMin: "",
  roundsMax: "",
  durationMin: "",
  durationMax: "",
  dateFrom: "",
  dateTo: "",
};

export interface DemoLibraryItem {
  id: number;
  [key: string]: unknown;
}

export interface LibraryRenameDraft {
  id: number;
  draft: string;
}

export interface LibraryDeletePrompt {
  id: number;
  label: string;
}

interface ProgressMeta {
  loading?: boolean;
  isError?: boolean;
}

type ReportProgress = (text: string, meta?: ProgressMeta) => void;

interface DemoLibraryControllerOptions {
  enabled: boolean;
  t: Translate;
  reportProgress: ReportProgress;
}

interface RefreshOptions {
  manageLoading?: boolean;
  searchQ?: string;
}

type QueryParams = Record<string, string | number>;

function nonNegativeInteger(value: unknown): number | null {
  const text = String(value ?? "").trim();
  if (!text) return null;
  const parsed = Number.parseInt(text, 10);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function nonNegativeNumber(value: unknown): number | null {
  const text = String(value ?? "").trim();
  if (!text) return null;
  const parsed = Number.parseFloat(text);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function dateBoundary(value: string, endOfDay: boolean): string | null {
  const date = value.trim();
  if (!date) return null;
  const local = new Date(`${date}T${endOfDay ? "23:59:59.999" : "00:00:00.000"}`);
  return Number.isNaN(local.getTime()) ? null : local.toISOString();
}

export function buildDemoLibraryFilterParams(filters: DemoLibraryFilters): QueryParams {
  const params: QueryParams = {};
  if (filters.mapName.trim()) params.map_name = filters.mapName.trim();
  if (filters.status && filters.status !== "all") params.status = filters.status;
  if (filters.playerQuery.trim()) params.player_query = filters.playerQuery.trim();
  if (filters.steamQuery.trim()) params.steam_query = filters.steamQuery.trim();

  const integerFilters = [
    ["min_kills", filters.minKills],
    ["max_deaths", filters.maxDeaths],
    ["min_assists", filters.minAssists],
    ["rounds_min", filters.roundsMin],
    ["rounds_max", filters.roundsMax],
  ] as const;
  for (const [key, value] of integerFilters) {
    const parsed = nonNegativeInteger(value);
    if (parsed != null) params[key] = parsed;
  }

  const decimalFilters = [
    ["min_kd", filters.minKd],
    ["duration_min", filters.durationMin],
    ["duration_max", filters.durationMax],
  ] as const;
  for (const [key, value] of decimalFilters) {
    const parsed = nonNegativeNumber(value);
    if (parsed != null) params[key] = parsed;
  }

  const from = dateBoundary(filters.dateFrom, false);
  if (from) params.date_from = from;
  const to = dateBoundary(filters.dateTo, true);
  if (to) params.date_to = to;
  return params;
}

export function hasActiveDemoLibraryFilters(filters: DemoLibraryFilters): boolean {
  return Object.entries(filters).some(([key, value]) =>
    key === "status" ? Boolean(value && value !== "all") : Boolean(String(value).trim()),
  );
}

function apiErrorMessage(error: unknown): string {
  const candidate = error as {
    response?: { data?: { detail?: unknown } };
    message?: unknown;
  };
  const detail = candidate?.response?.data?.detail;
  if (detail != null) {
    if (typeof detail === "string") return detail;
    try {
      return JSON.stringify(detail) || String(detail);
    } catch {
      return String(detail);
    }
  }
  if (candidate?.message != null) return String(candidate.message);
  return String(error);
}

export function useDemoLibraryController({
  enabled,
  t,
  reportProgress,
}: DemoLibraryControllerOptions) {
  const [demoLibraryItems, setDemoLibraryItems] = useState<DemoLibraryItem[]>([]);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [libraryScanning, setLibraryScanning] = useState(false);
  const [libraryPage, setLibraryPage] = useState(1);
  const libraryPageRef = useRef(1);
  const [libraryHasNextPage, setLibraryHasNextPage] = useState(false);
  const [libraryTotal, setLibraryTotal] = useState<number | null>(null);
  const [selectedLibraryDemoIds, setSelectedLibraryDemoIds] = useState<Set<number>>(new Set());
  const [libraryRename, setLibraryRename] = useState<LibraryRenameDraft | null>(null);
  const [libraryDeletePrompt, setLibraryDeletePrompt] = useState<LibraryDeletePrompt | null>(null);
  const [librarySearchInput, setLibrarySearchInput] = useState("");
  const [librarySearchQ, setLibrarySearchQ] = useState("");
  const [libraryAdvFilters, setLibraryAdvFilters] = useState<DemoLibraryFilters>({
    ...EMPTY_DEMO_LIBRARY_FILTERS,
  });
  const [libraryJumpDraft, setLibraryJumpDraft] = useState("");
  const [libraryPageSize, setLibraryPageSize] = useState(12);
  const libraryPageSizeEffectSkipRef = useRef(false);

  const libraryTotalPages =
    libraryTotal == null ? null : Math.max(1, Math.ceil(libraryTotal / libraryPageSize));
  const filterParams = useMemo(
    () => buildDemoLibraryFilterParams(libraryAdvFilters),
    [libraryAdvFilters],
  );
  const filtersKey = useMemo(() => JSON.stringify(filterParams), [filterParams]);
  const hasLibraryAdvancedFilters = useMemo(
    () => hasActiveDemoLibraryFilters(libraryAdvFilters),
    [libraryAdvFilters],
  );

  useEffect(() => {
    libraryPageRef.current = libraryPage;
  }, [libraryPage]);

  useEffect(() => {
    setLibraryPage(1);
  }, [filtersKey]);

  const refreshDemoLibrary = useCallback(
    async (page = libraryPageRef.current, options: RefreshOptions = {}) => {
      const { manageLoading = true, searchQ: searchOverride } = options;
      if (manageLoading) setLibraryLoading(true);
      try {
        const offset = (page - 1) * libraryPageSize;
        const search = searchOverride !== undefined ? searchOverride : librarySearchQ;
        const params: QueryParams = {
          limit: libraryPageSize,
          offset,
          ...filterParams,
        };
        if (search) params.q = search;
        const { data } = await API.get("/demos/compact", { params });
        const items = Array.isArray(data?.items) ? data.items : [];
        setDemoLibraryItems(items);
        const total = typeof data?.total === "number" ? data.total : null;
        setLibraryTotal(total);
        setLibraryHasNextPage(
          total == null ? items.length === libraryPageSize : offset + items.length < total,
        );
      } catch {
        // 后台 SSE 或启动阶段刷新失败时保留当前列表。
      } finally {
        if (manageLoading) setLibraryLoading(false);
      }
    },
    [filterParams, libraryPageSize, librarySearchQ],
  );

  const refreshDemoLibraryRef = useRef(refreshDemoLibrary);
  refreshDemoLibraryRef.current = refreshDemoLibrary;

  const handleLibrarySearchSubmit = useCallback(() => {
    const next = librarySearchInput.trim();
    setLibrarySearchQ(next);
    setLibraryPage(1);
    void refreshDemoLibrary(1, { manageLoading: true, searchQ: next });
  }, [librarySearchInput, refreshDemoLibrary]);

  useEffect(() => {
    if (!libraryPageSizeEffectSkipRef.current) {
      libraryPageSizeEffectSkipRef.current = true;
      return;
    }
    setLibraryPage(1);
    void refreshDemoLibraryRef.current(1, { manageLoading: false });
  }, [libraryPageSize]);

  useEffect(() => {
    if (!enabled) return;
    void refreshDemoLibrary(libraryPage, { manageLoading: false });
  }, [enabled, libraryPage, refreshDemoLibrary]);

  useEffect(() => {
    if (!enabled) return;
    const timer = window.setTimeout(() => {
      const next = librarySearchInput.trim();
      if (next === librarySearchQ) return;
      setLibrarySearchQ(next);
      setLibraryPage(1);
      void refreshDemoLibraryRef.current(1, { manageLoading: false, searchQ: next });
    }, 400);
    return () => window.clearTimeout(timer);
  }, [enabled, librarySearchInput, librarySearchQ]);

  useEffect(() => {
    let cancelled = false;
    let eventSource: EventSource | null = null;
    let debounce: number | null = null;

    const scheduleRefresh = () => {
      if (cancelled) return;
      if (debounce != null) window.clearTimeout(debounce);
      debounce = window.setTimeout(() => {
        void refreshDemoLibrary(libraryPageRef.current, { manageLoading: false });
      }, 600);
    };
    const connect = () => {
      if (cancelled) return;
      try {
        eventSource = new EventSource(`${API_BASE_URL}/api/demos/stream`);
      } catch {
        return;
      }
      eventSource.addEventListener("library", scheduleRefresh);
      eventSource.onerror = () => {
        if (cancelled) return;
        try {
          eventSource?.close();
        } catch {
          // ignore
        }
        eventSource = null;
        window.setTimeout(connect, 4000);
      };
    };

    connect();
    return () => {
      cancelled = true;
      if (debounce != null) window.clearTimeout(debounce);
      try {
        eventSource?.close();
      } catch {
        // ignore
      }
    };
  }, [refreshDemoLibrary]);

  const handleLibraryPageJump = useCallback(() => {
    const raw = libraryJumpDraft.trim();
    if (!raw) return;
    const parsed = Number.parseInt(raw, 10);
    if (!Number.isFinite(parsed) || parsed < 1) {
      reportProgress(t("app.libraryPageJumpInvalid"));
      return;
    }
    const target = libraryTotalPages != null ? Math.min(parsed, libraryTotalPages) : parsed;
    if (target !== parsed) {
      reportProgress(t("app.libraryPageJumpClamped", { maxPage: libraryTotalPages }));
    }
    setLibraryJumpDraft("");
    setLibraryPage(target);
    void refreshDemoLibrary(target, { manageLoading: false });
  }, [libraryJumpDraft, libraryTotalPages, refreshDemoLibrary, reportProgress, t]);

  const handleScanDemos = useCallback(async () => {
    setLibraryScanning(true);
    try {
      const { data } = await API.post("/demos/scan");
      await refreshDemoLibrary(libraryPageRef.current, { manageLoading: false });
      const discovered = data?.discovered_count;
      reportProgress(
        typeof discovered === "number" && discovered > 0
          ? t("app.scanDone", { n: discovered })
          : t("app.scanDoneEmpty", { scanned: data?.scanned || 0 }),
      );
      return data;
    } catch (error) {
      reportProgress(t("app.scanFail", { msg: apiErrorMessage(error) }), { isError: true });
      return null;
    } finally {
      setLibraryScanning(false);
    }
  }, [refreshDemoLibrary, reportProgress, t]);

  const handleDeleteDemo = useCallback(async (id: number, rescan: string) => {
    try {
      await API.delete(`/demos/${id}`, { params: { rescan } });
      setLibraryDeletePrompt(null);
      await refreshDemoLibrary(libraryPageRef.current, { manageLoading: false });
    } catch (error) {
      reportProgress(t("app.deleteFail", { msg: apiErrorMessage(error) }), { isError: true });
    }
  }, [refreshDemoLibrary, reportProgress, t]);

  const handleDeleteDemoFile = useCallback(async (id: number) => {
    try {
      await API.post(`/demos/${id}/delete-file`);
      setLibraryDeletePrompt(null);
      reportProgress(t("app.deleteFileDone"));
      await refreshDemoLibrary(libraryPageRef.current, { manageLoading: false });
    } catch (error) {
      reportProgress(t("app.deleteFileFail", { msg: apiErrorMessage(error) }), { isError: true });
    }
  }, [refreshDemoLibrary, reportProgress, t]);

  const handleLibraryBatchDelete = useCallback(async (ids: Iterable<number>, rescan = "skip") => {
    const list = [...ids];
    if (!list.length) return;
    reportProgress(t("app.batchDeleteProgress", { done: 0, total: list.length }), { loading: true });
    let done = 0;
    for (const id of list) {
      try {
        await API.delete(`/demos/${id}`, { params: { rescan } });
        done += 1;
        reportProgress(t("app.batchDeleteProgress", { done, total: list.length }), { loading: true });
      } catch (error) {
        reportProgress(t("app.batchDeleteFail", { msg: apiErrorMessage(error) }), { isError: true });
        await refreshDemoLibrary(libraryPageRef.current, { manageLoading: false });
        return;
      }
    }
    setSelectedLibraryDemoIds(new Set());
    reportProgress(t("app.batchDeleteDone", { n: list.length }));
    await refreshDemoLibrary(libraryPageRef.current, { manageLoading: false });
  }, [refreshDemoLibrary, reportProgress, t]);

  const handleSaveLibraryRename = useCallback(async () => {
    if (!libraryRename) return;
    try {
      await API.patch(`/demos/${libraryRename.id}`, { display_name: libraryRename.draft });
      setLibraryRename(null);
      await refreshDemoLibrary(libraryPageRef.current, { manageLoading: false });
    } catch (error) {
      reportProgress(t("app.renameFail", { msg: apiErrorMessage(error) }), { isError: true });
    }
  }, [libraryRename, refreshDemoLibrary, reportProgress, t]);

  const selectLibraryPage = useCallback(() => {
    setSelectedLibraryDemoIds((previous) => {
      const next = new Set(previous);
      for (const item of demoLibraryItems) next.add(item.id);
      return next;
    });
  }, [demoLibraryItems]);

  const selectAllLibraryDemos = useCallback(async () => {
    try {
      const cap = 1000;
      const limit = libraryTotal != null ? Math.min(libraryTotal, cap) : cap;
      const params: QueryParams = { limit, offset: 0, ...filterParams };
      if (librarySearchQ) params.q = librarySearchQ;
      const { data } = await API.get("/demos/ids", { params });
      const ids = Array.isArray(data?.ids)
        ? data.ids.map(Number).filter((id: number) => Number.isFinite(id))
        : [];
      setSelectedLibraryDemoIds(new Set(ids));
      if (libraryTotal != null && libraryTotal > cap) {
        reportProgress(t("app.librarySelectAllCapped", { cap }));
      }
    } catch (error) {
      reportProgress(t("app.librarySelectAllFail", { msg: apiErrorMessage(error) }), { isError: true });
    }
  }, [filterParams, librarySearchQ, libraryTotal, reportProgress, t]);

  const clearLibrarySelection = useCallback(() => setSelectedLibraryDemoIds(new Set()), []);

  return {
    demoLibraryItems,
    libraryLoading,
    libraryScanning,
    libraryPage,
    setLibraryPage,
    libraryHasNextPage,
    libraryTotal,
    selectedLibraryDemoIds,
    setSelectedLibraryDemoIds,
    libraryRename,
    setLibraryRename,
    libraryDeletePrompt,
    setLibraryDeletePrompt,
    librarySearchInput,
    setLibrarySearchInput,
    librarySearchQ,
    setLibrarySearchQ,
    libraryAdvFilters,
    setLibraryAdvFilters,
    libraryJumpDraft,
    setLibraryJumpDraft,
    libraryPageSize,
    setLibraryPageSize,
    libraryTotalPages,
    hasLibraryAdvancedFilters,
    refreshDemoLibrary,
    handleLibrarySearchSubmit,
    handleLibraryPageJump,
    handleScanDemos,
    handleDeleteDemo,
    handleDeleteDemoFile,
    handleLibraryBatchDelete,
    handleSaveLibraryRename,
    selectLibraryPage,
    selectAllLibraryDemos,
    clearLibrarySelection,
  };
}
