import { Loader2 } from "lucide-react";

import { BACKEND_CONNECT_LABEL } from "../api/api";

export type StartupInitPhase = "update" | "config" | null;

interface StartupGateProps {
  backendReady: boolean;
  startupInitDone: boolean;
  startupInitPhase: StartupInitPhase;
  standalonePreview: boolean;
  t: (key: string) => string;
}

export default function StartupGate({
  backendReady,
  startupInitDone,
  startupInitPhase,
  standalonePreview,
  t,
}: StartupGateProps) {
  if (standalonePreview || (backendReady && startupInitDone)) return null;

  if (!backendReady) {
    return (
      <div className="absolute inset-0 z-50 flex flex-col items-center justify-center bg-cs2-bg-dark/80 backdrop-blur-sm">
        <div className="flex flex-col items-center gap-6 p-8 rounded-2xl border border-white/5 bg-cs2-bg-card shadow-2xl">
          <div className="relative">
            <Loader2 className="h-12 w-12 animate-spin text-cs2-orange" />
            <div className="absolute inset-0 animate-ping rounded-full bg-cs2-orange/20" />
          </div>
          <div className="flex flex-col items-center gap-2">
            <h2 className="text-xl font-bold tracking-tight text-dynamic-white">
              {t("app.backendConnecting")}
            </h2>
            <p className="text-sm text-dynamic-zinc-400">{t("app.backendStarting")}</p>
          </div>
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-black/40 border border-white/5">
            <div className="w-1.5 h-1.5 rounded-full bg-cs2-orange animate-pulse" />
            <span className="text-[10px] font-mono text-zinc-500 uppercase tracking-widest">
              Attempting to connect: {BACKEND_CONNECT_LABEL}
            </span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="absolute inset-0 z-50 flex flex-col items-center justify-center bg-cs2-bg-dark/80 backdrop-blur-sm">
      <div className="flex flex-col items-center gap-6 p-8 rounded-2xl border border-white/5 bg-cs2-bg-card shadow-2xl">
        <Loader2 className="h-12 w-12 animate-spin text-cs2-orange" />
        <div className="flex flex-col items-center gap-2">
          <h2 className="text-xl font-bold tracking-tight text-dynamic-white">
            {startupInitPhase === "config"
              ? t("app.startupCheckingConfig")
              : t("app.startupCheckingUpdate")}
          </h2>
          <p className="text-sm text-dynamic-zinc-400">{t("app.startupPleaseWait")}</p>
        </div>
      </div>
    </div>
  );
}
