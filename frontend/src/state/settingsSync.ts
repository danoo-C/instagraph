import { useEffect } from "react";
import { apiFetch } from "@/api/client";
import type { components } from "@/api/schema";
import { useI18nStore, type Language } from "@/i18n";
import { useThemeStore, type ThemePreference } from "@/state/theme";

type AppSettings = components["schemas"]["AppSettings"];

const DEFAULT_THEME: ThemePreference = "system";
const DEFAULT_LANGUAGE: Language = "en";

/**
 * Apply the backend's saved theme / UI language to the local stores, but only where the
 * local value is still the default (a choice made in this browser wins). Pure for tests.
 */
export function applyBackendPreferences(s: Pick<AppSettings, "theme" | "ui_language">): void {
  const theme = useThemeStore.getState();
  if (theme.theme === DEFAULT_THEME && s.theme && s.theme !== DEFAULT_THEME)
    theme.setTheme(s.theme);
  const i18n = useI18nStore.getState();
  if (i18n.language === DEFAULT_LANGUAGE && s.ui_language && s.ui_language !== DEFAULT_LANGUAGE)
    i18n.setLanguage(s.ui_language);
}

/** On startup, fetch `/api/settings` once and apply theme / UI language (§ settings). */
export function useBackendPreferences(): void {
  useEffect(() => {
    const ctrl = new AbortController();
    apiFetch<AppSettings>("GET", "/api/settings", { signal: ctrl.signal })
      .then(applyBackendPreferences)
      .catch(() => {
        /* offline / no backend: keep local preferences */
      });
    return () => ctrl.abort();
  }, []);
}
