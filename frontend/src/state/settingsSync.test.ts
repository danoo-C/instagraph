import { beforeEach, describe, expect, it } from "vitest";
import { useI18nStore } from "@/i18n";
import { applyBackendPreferences } from "@/state/settingsSync";
import { useThemeStore } from "@/state/theme";

describe("applyBackendPreferences", () => {
  beforeEach(() => {
    useThemeStore.setState({ theme: "system" });
    useI18nStore.setState({ language: "en" });
  });

  it("applies backend values while the local stores are at their defaults", () => {
    applyBackendPreferences({ theme: "dark", ui_language: "sk" });
    expect(useThemeStore.getState().theme).toBe("dark");
    expect(useI18nStore.getState().language).toBe("sk");
  });

  it("keeps a local choice", () => {
    useThemeStore.setState({ theme: "light" });
    useI18nStore.setState({ language: "sk" });
    applyBackendPreferences({ theme: "dark", ui_language: "en" });
    expect(useThemeStore.getState().theme).toBe("light");
    expect(useI18nStore.getState().language).toBe("sk");
  });
});
