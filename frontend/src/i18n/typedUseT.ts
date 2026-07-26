import { useCallback } from "react";

import { useLocaleStore } from "./localeStore.js";
import en from "./dict/en.js";
import zh from "./dict/zh.js";

export type TranslationParams = Record<string, unknown>;
export type Translate = (key: string, params?: TranslationParams) => string;

const DICTS = {
  zh: zh as Record<string, string>,
  en: en as Record<string, string>,
} as const;

function interpolate(text: string, params?: TranslationParams): string {
  if (!params) return text;
  return text.replace(/\{(\w+)\}/g, (match, key: string) =>
    Object.prototype.hasOwnProperty.call(params, key) ? String(params[key]) : match,
  );
}

export function translate(locale: unknown, key: string, params?: TranslationParams): string {
  const dict = locale === "en" ? DICTS.en : DICTS.zh;
  const value = dict[key] ?? DICTS.zh[key];
  if (value === undefined) {
    if (import.meta.env.DEV) console.warn(`[i18n] missing key: ${key}`);
    return key;
  }
  return interpolate(value, params);
}

export function useT(): Translate {
  const effectiveLocale = useLocaleStore((state) => state.effectiveLocale);
  return useCallback(
    (key: string, params?: TranslationParams) => translate(effectiveLocale, key, params),
    [effectiveLocale],
  );
}
