import { create } from "zustand";

export const useMontageStore = create((set) => ({
  exporting: false,
  setExporting: (v) => set({ exporting: v }),

  /** null | { job_id, status, stage, processed, total, fraction, updated_at } */
  exportProgress: null,
  setExportProgress: (v) =>
    set((s) => ({
      exportProgress:
        typeof v === "function" ? v(s.exportProgress) : v ? { ...v } : null,
    })),

  /** null | { ok: boolean, err?: string, output_path?: string, unread?: boolean } */
  lastExport: null,
  setLastExport: (v) =>
    set({ lastExport: v ? { ...v, unread: v.unread ?? true } : null }),
  markExportRead: () =>
    set((s) => ({ lastExport: s.lastExport ? { ...s.lastExport, unread: false } : null })),
}));
