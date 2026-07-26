import { useCallback, useEffect, useRef, useState } from "react";
import { getVersion as getDesktopAppVersion } from "@tauri-apps/api/app";

import API from "../api/api";
import { shouldCheckAppUpdates } from "../utils/shouldCheckAppUpdates";
import {
  createDesktopUpdateCheck,
  type DesktopUpdateController,
  type DesktopUpdateStatus,
  type UpdateMode,
  type UpdateStatus,
} from "../utils/desktopUpdater";

type Translate = (key: string) => string;

export interface UpdateInfo {
  status: UpdateStatus;
  current_version: string;
  latest_version: string | null;
  release_notes?: string;
  update_mode: UpdateMode;
  progress?: DesktopUpdateStatus["progress"] | null;
  error?: string;
}

export interface FetchUpdateOptions {
  manual?: boolean;
  awaitDismiss?: boolean;
}

export type FetchUpdateInfo = (options?: FetchUpdateOptions) => Promise<void>;

interface DesktopUpdaterState {
  updateInfo: UpdateInfo | null;
  updateModalOpen: boolean;
  updateModalManual: boolean;
  fetchUpdateInfo: FetchUpdateInfo;
  handleUpdateModalClose(): void;
  handleUpdateConfirm(): void;
  handleUpdateCancel(): void;
}

export function useDesktopUpdater(t: Translate): DesktopUpdaterState {
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);
  const [updateModalOpen, setUpdateModalOpen] = useState(false);
  const [updateModalManual, setUpdateModalManual] = useState(false);
  const updateCheckOptsRef = useRef<Required<FetchUpdateOptions>>({
    manual: false,
    awaitDismiss: false,
  });
  const updateControllerRef = useRef<DesktopUpdateController | null>(null);
  const updateModalDismissedRef = useRef(false);
  const startupUpdateWaitRef = useRef<(() => void) | null>(null);

  const resumeStartup = useCallback(() => {
    const resume = startupUpdateWaitRef.current;
    startupUpdateWaitRef.current = null;
    resume?.();
  }, []);

  const waitForUpdateModalDismiss = useCallback(
    () =>
      new Promise<void>((resolve) => {
        startupUpdateWaitRef.current = resolve;
      }),
    [],
  );

  const markUpdateChecked = useCallback(async () => {
    try {
      await API.put("config", { last_update_check_at: new Date().toISOString() });
    } catch {
      // 更新检查结果不应被配置写回失败覆盖。
    }
  }, []);

  const handleUpdateModalClose = useCallback(() => {
    const status = updateInfo?.status;
    const isForce = updateInfo?.update_mode === "force";
    if (
      isForce &&
      (status === "available" || status === "downloading" || status === "downloaded")
    ) {
      return;
    }

    updateModalDismissedRef.current = true;
    if (status === "checking" || status === "available") {
      updateControllerRef.current?.defer();
    }
    setUpdateModalOpen(false);
    setUpdateModalManual(false);
    resumeStartup();
  }, [resumeStartup, updateInfo?.status, updateInfo?.update_mode]);

  const handleUpdateConfirm = useCallback(() => {
    updateControllerRef.current?.confirm();
  }, []);

  const handleUpdateCancel = useCallback(() => {
    if (updateInfo?.update_mode === "force") return;
    updateModalDismissedRef.current = false;
    updateControllerRef.current?.cancel();
  }, [updateInfo?.update_mode]);

  const fetchUpdateInfo = useCallback<FetchUpdateInfo>(
    async (options = {}) => {
      const manual = Boolean(options.manual);
      const awaitDismiss = Boolean(options.awaitDismiss);

      if (!(await shouldCheckAppUpdates())) {
        if (manual) {
          setUpdateInfo({
            status: "error",
            error: t("settings.updateDevModeError"),
            current_version: "",
            latest_version: null,
            update_mode: "normal",
          });
          setUpdateModalManual(true);
          setUpdateModalOpen(true);
        }
        return;
      }

      updateCheckOptsRef.current = { manual, awaitDismiss };
      updateModalDismissedRef.current = false;

      let currentVersion = "";
      try {
        currentVersion = String((await getDesktopAppVersion()) || "");
      } catch {
        currentVersion = "";
      }

      updateControllerRef.current?.cancel();

      const controller = createDesktopUpdateCheck((statusPayload) => {
        if (updateControllerRef.current !== controller) return;

        const status = statusPayload.status;
        setUpdateInfo((previous) => ({
          status,
          current_version: currentVersion || previous?.current_version || "",
          latest_version: statusPayload.latest_version || previous?.latest_version || null,
          release_notes: statusPayload.release_notes || previous?.release_notes || "",
          update_mode: statusPayload.update_mode || previous?.update_mode || "normal",
          progress: statusPayload.progress ?? null,
          error:
            statusPayload.error === "dev-mode"
              ? t("settings.updateDevModeError")
              : statusPayload.error || "",
        }));

        const isManual = updateCheckOptsRef.current.manual;
        if (status === "checking") {
          if (isManual) {
            setUpdateModalManual(true);
            setUpdateModalOpen(true);
          }
          return;
        }

        if (status === "available" || status === "downloading" || status === "downloaded") {
          if (status === "available") void markUpdateChecked();
          setUpdateModalManual(isManual);
          setUpdateModalOpen(true);
          return;
        }

        if (status === "cancelled") {
          if (updateModalDismissedRef.current) {
            setUpdateModalOpen(false);
            resumeStartup();
          } else if (isManual) {
            setUpdateModalManual(true);
            setUpdateModalOpen(true);
          } else {
            setUpdateModalOpen(false);
            resumeStartup();
          }
          return;
        }

        if (status === "not-available") {
          void markUpdateChecked();
          if (isManual) {
            setUpdateModalManual(true);
            setUpdateModalOpen(true);
          } else {
            resumeStartup();
          }
          return;
        }

        if (status === "error") {
          if (isManual) {
            setUpdateModalManual(true);
            setUpdateModalOpen(true);
          } else {
            resumeStartup();
          }
        }
      });
      updateControllerRef.current = controller;

      setUpdateInfo({
        status: "checking",
        current_version: currentVersion,
        latest_version: null,
        release_notes: "",
        update_mode: "normal",
        error: "",
      });
      if (manual) {
        setUpdateModalManual(true);
        setUpdateModalOpen(true);
      }

      const dismissWait = awaitDismiss ? waitForUpdateModalDismiss() : null;
      controller.start();
      if (dismissWait) await dismissWait;
    },
    [markUpdateChecked, resumeStartup, t, waitForUpdateModalDismiss],
  );

  useEffect(
    () => () => {
      updateControllerRef.current?.cancel();
      updateControllerRef.current = null;
    },
    [],
  );

  return {
    updateInfo,
    updateModalOpen,
    updateModalManual,
    fetchUpdateInfo,
    handleUpdateModalClose,
    handleUpdateConfirm,
    handleUpdateCancel,
  };
}
