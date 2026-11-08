import { create } from "zustand";
import { persist } from "zustand/middleware";
import { en, type TranslationKey } from "@/i18n/en";
import { sk } from "@/i18n/sk";

export type Language = "en" | "sk";
export type { TranslationKey };

const dictionaries: Record<Language, Partial<Record<TranslationKey, string>>> = { en, sk };

interface I18nState {
  language: Language;
  setLanguage: (l: Language) => void;
}

export const useI18nStore = create<I18nState>()(
  persist((set) => ({ language: "en", setLanguage: (language) => set({ language }) }), {
    name: "instagraph.language",
  }),
);

/** Translate a key with `{var}` interpolation, falling back to English then the key. */
export function translate(
  language: Language,
  key: TranslationKey,
  vars?: Record<string, string | number>,
): string {
  const raw = dictionaries[language][key] ?? en[key] ?? key;
  if (!vars) return raw;
  return raw.replace(/\{(\w+)\}/g, (m, name: string) => (name in vars ? String(vars[name]) : m));
}

/** Non-reactive translate using the current language (for use outside React). */
export function t(key: TranslationKey, vars?: Record<string, string | number>): string {
  return translate(useI18nStore.getState().language, key, vars);
}

/** Reactive hook: `const t = useT(); t("nav.overview")`. Re-renders on language change. */
export function useT(): typeof t {
  const language = useI18nStore((s) => s.language);
  return (key, vars) => translate(language, key, vars);
}
