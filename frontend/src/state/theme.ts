import { useEffect, useSyncExternalStore } from "react";
import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

interface ThemeState {
  theme: ThemePreference;
  setTheme: (t: ThemePreference) => void;
}

export const useThemeStore = create<ThemeState>()(
  persist((set) => ({ theme: "system", setTheme: (theme) => set({ theme }) }), {
    name: "instagraph.theme",
  }),
);

const media = () =>
  typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;

function subscribeSystem(cb: () => void) {
  const m = media();
  m?.addEventListener("change", cb);
  return () => m?.removeEventListener("change", cb);
}

/** The effective theme ("light" | "dark"), following the OS when preference is "system". */
export function useResolvedTheme(): ResolvedTheme {
  const pref = useThemeStore((s) => s.theme);
  const systemDark = useSyncExternalStore(
    subscribeSystem,
    () => media()?.matches ?? false,
    () => false,
  );
  if (pref === "system") return systemDark ? "dark" : "light";
  return pref;
}

/** Applies the `dark` class on <html>. Mount once (in providers). */
export function useApplyTheme(): void {
  const theme = useResolvedTheme();
  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    root.style.colorScheme = theme;
  }, [theme]);
}
