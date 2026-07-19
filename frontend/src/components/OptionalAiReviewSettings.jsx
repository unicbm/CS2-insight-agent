import { Brain, Zap } from "lucide-react";

function llmBaseUrlLooksLocal(baseUrl) {
  try {
    const raw = String(baseUrl || "").trim();
    if (!raw) return false;
    const url = new URL(raw.includes("://") ? raw : `http://${raw}`);
    const host = url.hostname.toLowerCase();
    return (
      host === "localhost" ||
      host === "127.0.0.1" ||
      host === "::1" ||
      host.endsWith(".localhost")
    );
  } catch {
    return false;
  }
}

export default function OptionalAiReviewSettings({
  enabled,
  onEnabledChange,
  llm,
  onLlmChange,
  t,
}) {
  const value = llm ?? {};
  const updateLlm = (key, nextValue) => {
    onLlmChange?.({ ...value, [key]: nextValue });
  };
  const setEnabled = (nextEnabled) => {
    if (Boolean(enabled) !== nextEnabled) onEnabledChange?.(nextEnabled);
  };
  const localEndpoint = llmBaseUrlLooksLocal(value.base_url);

  return (
    <section className="overflow-hidden rounded-xl border border-cs2-border/70 bg-cs2-bg-card">
      <div className="px-4 py-3.5">
        <div className="mb-2.5 flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <h2 className="text-sm font-bold uppercase tracking-wide text-cs2-text-secondary">
            {t("settings.sectionAnalysisMode")}
          </h2>
          <span className="text-xs text-cs2-text-muted">
            {t("settings.sectionAnalysisModeHint")}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-2" role="group" aria-label={t("settings.sectionAnalysisMode")}>
          <button
            type="button"
            aria-pressed={!enabled}
            onClick={() => setEnabled(false)}
            className={`flex min-w-0 items-start gap-2 rounded-lg border px-3 py-2.5 text-left transition-colors ${
              !enabled
                ? "border-cs2-accent/60 bg-cs2-accent/10"
                : "border-cs2-border bg-cs2-bg-input/30 hover:border-cs2-accent/30"
            }`}
          >
            <Zap className={`mt-0.5 h-4 w-4 shrink-0 ${!enabled ? "text-cs2-accent" : "text-cs2-text-muted"}`} />
            <span className="min-w-0">
              <span className="block text-xs font-semibold text-cs2-text-primary">
                {t("settings.modeLocal")}
              </span>
              <span className="mt-0.5 block text-[11px] leading-snug text-cs2-text-muted">
                {t("settings.modeLocalDesc")}
              </span>
            </span>
          </button>

          <button
            type="button"
            aria-pressed={Boolean(enabled)}
            onClick={() => setEnabled(true)}
            className={`flex min-w-0 items-start gap-2 rounded-lg border px-3 py-2.5 text-left transition-colors ${
              enabled
                ? "border-cs2-accent/60 bg-cs2-accent/10"
                : "border-cs2-border bg-cs2-bg-input/30 hover:border-cs2-accent/30"
            }`}
          >
            <Brain className={`mt-0.5 h-4 w-4 shrink-0 ${enabled ? "text-cs2-accent" : "text-cs2-text-muted"}`} />
            <span className="min-w-0">
              <span className="block text-xs font-semibold text-cs2-text-primary">
                {t("settings.modeAi")}
              </span>
              <span className="mt-0.5 block text-[11px] leading-snug text-cs2-text-muted">
                {t("settings.modeAiDesc")}
              </span>
            </span>
          </button>
        </div>
      </div>

      {enabled ? (
        <div className="border-t border-cs2-border/60 bg-cs2-bg-input/20 px-4 py-3.5">
          <div className="mb-3">
            <h3 className="text-xs font-bold uppercase tracking-wide text-cs2-text-secondary">
              {t("settings.sectionLlm")}
            </h3>
            <p className="mt-1 text-[11px] leading-relaxed text-cs2-text-muted">
              {t("settings.sectionLlmHint")}
            </p>
          </div>

          {localEndpoint ? (
            <p className="mb-3 rounded-md border border-cs2-accent/30 bg-cs2-accent/5 px-3 py-2 text-[11px] text-cs2-accent">
              {t("settings.localEndpointHint")}
            </p>
          ) : null}

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block sm:col-span-2" htmlFor="optional-ai-base-url">
              <span className="mb-1 block text-xs font-semibold text-cs2-text-secondary">
                {t("settings.labelLlmBaseUrl")}
              </span>
              <input
                id="optional-ai-base-url"
                type="text"
                value={value.base_url ?? ""}
                onChange={(event) => updateLlm("base_url", event.target.value || null)}
                placeholder={t("settings.baseUrlPlaceholder")}
                className="w-full rounded-md border border-cs2-border bg-cs2-bg-input px-3 py-2 text-xs text-cs2-text-primary placeholder:text-cs2-text-muted focus-visible:border-cs2-accent focus-visible:outline-none"
              />
            </label>

            <label className="block" htmlFor="optional-ai-model">
              <span className="mb-1 block text-xs font-semibold text-cs2-text-secondary">
                {t("settings.labelLlmModel")}
              </span>
              <input
                id="optional-ai-model"
                type="text"
                value={value.model ?? ""}
                onChange={(event) => updateLlm("model", event.target.value)}
                placeholder={t("settings.modelPlaceholder")}
                className="w-full rounded-md border border-cs2-border bg-cs2-bg-input px-3 py-2 text-xs text-cs2-text-primary placeholder:text-cs2-text-muted focus-visible:border-cs2-accent focus-visible:outline-none"
              />
            </label>

            <label className="block" htmlFor="optional-ai-api-key">
              <span className="mb-1 block text-xs font-semibold text-cs2-text-secondary">
                {t("settings.labelLlmApiKey")}
              </span>
              <input
                id="optional-ai-api-key"
                type="password"
                value={value.api_key ?? ""}
                onChange={(event) => updateLlm("api_key", event.target.value)}
                placeholder={t("settings.apiKeyPlaceholderKeep")}
                autoComplete="off"
                className="w-full rounded-md border border-cs2-border bg-cs2-bg-input px-3 py-2 text-xs text-cs2-text-primary placeholder:text-cs2-text-muted focus-visible:border-cs2-accent focus-visible:outline-none"
              />
            </label>
          </div>
        </div>
      ) : null}
    </section>
  );
}
