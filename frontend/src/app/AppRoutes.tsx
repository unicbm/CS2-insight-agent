import { lazy, Suspense } from "react";
import { Loader2 } from "lucide-react";
import { Navigate, Route, Routes } from "react-router-dom";

const GuidePage = lazy(() => import("../pages/GuidePage"));
const DemoLibraryPage = lazy(() => import("../pages/DemoLibraryPage"));
const DemoAnalysisPreviewPage = lazy(() => import("../pages/DemoAnalysisPreviewPage"));
const RecordingQueuePage = lazy(() => import("../pages/RecordingQueuePage"));
const MontageWorkbenchPage = lazy(() => import("../pages/MontageWorkbenchPage"));
const LiteCutEditorPage = lazy(() => import("../pages/liteCut/LiteCutEditorPage"));
const LiteCutExportPage = lazy(() => import("../pages/liteCut/LiteCutExportPage"));
const RecordingParamsPage = lazy(() => import("../pages/RecordingParamsPage"));
const SettingsPage = lazy(() => import("../pages/SettingsPage"));
const PlayerGameConfigPage = lazy(() => import("../pages/PlayerGameConfigPage"));
const MatchHistoryPage = lazy(() => import("../pages/MatchHistoryPage"));
const ObsAiTuningPreviewPage = lazy(() => import("../pages/ObsAiTuningPreviewPage"));
const ObsAiEntryPreviewPage = lazy(() => import("../pages/ObsAiEntryPreviewPage"));

const PAGE_ROUTES = [
  { path: "/", Page: GuidePage },
  { path: "/library", Page: DemoLibraryPage },
  { path: "/analysis", Page: DemoAnalysisPreviewPage },
  { path: "/queue", Page: RecordingQueuePage },
  { path: "/montage", Page: MontageWorkbenchPage },
  { path: "/lite-cut", Page: LiteCutEditorPage },
  { path: "/lite-cut/export", Page: LiteCutExportPage },
  { path: "/params", Page: RecordingParamsPage },
  { path: "/settings", Page: SettingsPage },
  { path: "/player-game-config", Page: PlayerGameConfigPage },
  { path: "/match-history", Page: MatchHistoryPage },
  { path: "/obs-ai-entry-preview", Page: ObsAiEntryPreviewPage },
  { path: "/obs-ai-preview", Page: ObsAiTuningPreviewPage },
] as const;

const LEGACY_REDIRECTS = [
  { from: "/demo-analysis-preview", to: "/analysis" },
  { from: "/lite-cut/editor", to: "/lite-cut" },
  { from: "/lite-cut/text", to: "/lite-cut" },
  { from: "/lite-cut/color", to: "/lite-cut" },
] as const;

export default function AppRoutes() {
  return (
    <Suspense
      fallback={
        <div
          className="flex min-h-0 flex-1 items-center justify-center"
          aria-label="正在加载页面"
        >
          <Loader2 className="h-7 w-7 animate-spin text-cs2-orange" />
        </div>
      }
    >
      <Routes>
        {PAGE_ROUTES.map(({ path, Page }) => (
          <Route key={path} path={path} element={<Page />} />
        ))}
        {LEGACY_REDIRECTS.map(({ from, to }) => (
          <Route key={from} path={from} element={<Navigate to={to} replace />} />
        ))}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
