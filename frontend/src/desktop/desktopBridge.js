import { getVersion } from "@tauri-apps/api/app";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { open } from "@tauri-apps/plugin-dialog";
import { openUrl } from "@tauri-apps/plugin-opener";

export const isDesktopApp = Boolean(window.__TAURI_INTERNALS__);

const currentWindow = isDesktopApp ? getCurrentWindow() : null;

export const desktopBridge = isDesktopApp
  ? {
      minimize: () => currentWindow.minimize(),
      async toggleMaximize() {
        if (await currentWindow.isMaximized()) {
          await currentWindow.unmaximize();
        } else {
          await currentWindow.maximize();
        }
      },
      close: () => currentWindow.close(),
      isMaximized: () => currentWindow.isMaximized(),
      onMaximizeChange(callback) {
        let active = true;
        const unlistenPromise = currentWindow.onResized(async () => {
          if (active) callback(await currentWindow.isMaximized());
        });
        return () => {
          active = false;
          void unlistenPromise.then((unlisten) => unlisten());
        };
      },
      getVersion,
      async showOpenDialog(options = {}) {
        const properties = Array.isArray(options.properties) ? options.properties : [];
        const selected = await open({
          title: options.title,
          defaultPath: options.defaultPath,
          filters: options.filters,
          directory: properties.includes("openDirectory"),
          multiple: properties.includes("multiSelections"),
        });
        const filePaths = selected == null ? [] : Array.isArray(selected) ? selected : [selected];
        return { canceled: filePaths.length === 0, filePaths };
      },
      openExternal: (url) => openUrl(url),
    }
  : null;
