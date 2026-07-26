import { Loader2 } from "lucide-react";

import type { StartupInitPhase } from "../hooks/useStartupInitialization";
import type { Translate } from "../i18n/typedUseT";

interface StartupStatusProps {
  backendReady: boolean;
  startupInitDone: boolean;
  startupInitPhase: StartupInitPhase;
  standalonePreview: boolean;
  t: Translate;
}

export default function StartupStatus({
  backendReady,
  startupInitDone,
  startupInitPhase,
  standalonePreview,
  t,
}: StartupStatusProps) {
  if (standalonePreview || (backendReady && startupInitDone)) return null;

  const label = !backendReady
    ? t("app.backendStarting")
    : startupInitPhase === "update"
      ? t("app.startupCheckingUpdate")
      : startupInitPhase === "config"
        ? t("app.startupCheckingConfig")
        : t("app.startupPreparing");

  return (
    <div
      className="pointer-events-none absolute inset-x-0 top-3 z-40 flex justify-center px-4"
      role="status"
      aria-live="polite"
    >
      <div className="flex max-w-full items-center gap-2 rounded-full border border-white/10 bg-cs2-bg-card/95 px-3 py-2 text-sm text-dynamic-zinc-200 shadow-xl backdrop-blur-md">
        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-cs2-orange" />
        <span className="truncate">{label}</span>
      </div>
    </div>
  );
}
