import { useEffect, useRef, useState } from "react";

import API from "../api/api";
import { shouldCheckAppUpdates } from "../utils/shouldCheckAppUpdates";
import type { FetchUpdateInfo } from "./useDesktopUpdater";

export type StartupInitPhase = "update" | "config" | null;

interface StartupInitializationOptions {
  backendReady: boolean;
  fetchUpdateInfo: FetchUpdateInfo;
}

interface StartupInitializationState {
  startupInitDone: boolean;
  startupInitPhase: StartupInitPhase;
  initialQuickCheckStatus: unknown;
}

export function useStartupInitialization({
  backendReady,
  fetchUpdateInfo,
}: StartupInitializationOptions): StartupInitializationState {
  const [startupInitDone, setStartupInitDone] = useState(false);
  const [startupInitPhase, setStartupInitPhase] = useState<StartupInitPhase>(null);
  const [initialQuickCheckStatus, setInitialQuickCheckStatus] = useState<unknown>(null);
  const startupInitStartedRef = useRef(false);

  useEffect(() => {
    if (!backendReady || startupInitStartedRef.current) return;
    startupInitStartedRef.current = true;

    let cancelled = false;
    const runStartupInit = async () => {
      try {
        if (await shouldCheckAppUpdates()) {
          setStartupInitPhase("update");
          await fetchUpdateInfo({ manual: false, awaitDismiss: true });
          if (cancelled) return;
        }

        setStartupInitPhase("config");
        try {
          const { data } = await API.get("/config/quick-check");
          if (!cancelled) setInitialQuickCheckStatus(data);
        } catch {
          if (!cancelled) setInitialQuickCheckStatus(null);
        }
      } finally {
        if (!cancelled) {
          setStartupInitPhase(null);
          setStartupInitDone(true);
        }
      }
    };

    void runStartupInit();
    return () => {
      cancelled = true;
    };
  }, [backendReady, fetchUpdateInfo]);

  return { startupInitDone, startupInitPhase, initialQuickCheckStatus };
}
