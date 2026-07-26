import "./index.css";
import { hydrateDesktopBootstrapLocale } from "./i18n/localeStore";
import { restoreLegacyElectronUiState } from "./utils/legacyElectronUiState";

Promise.allSettled([
  restoreLegacyElectronUiState(),
  hydrateDesktopBootstrapLocale(),
]).finally(() => import("./renderApp.jsx"));
