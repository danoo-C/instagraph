import { describe, expect, it } from "vitest";
import {
  deepEqual,
  diffSettings,
  formatBytes,
  invalidPatterns,
  isRedactedValue,
  parseLines,
  setLangList,
  touchesDerived,
  type AppSettings,
} from "@/features/settings/diff";
import { exportHref } from "@/features/settings/constants";

const base: AppSettings = {
  owner_person_id: null,
  owner_name: null,
  people_merges: [],
  thread_merges: [],
  thread_splits: [],
  timezone: null,
  session_gap_min: 60,
  reply_window_h: 12,
  enabled_locales: null,
  stopwords_added: {},
  stopwords_removed: {},
  ignored_words: [],
  laughter_patterns: ["lol"],
  swear_words_enabled: false,
  swear_words: [],
  custom_terms: {},
  emoji_merge_skin_tones: true,
  emoji_merge_gendered: false,
  anonymize: false,
  theme: "system",
  ui_language: "en",
};

describe("diffSettings", () => {
  it("returns only changed keys", () => {
    expect(diffSettings(base, { ...base })).toEqual({});
    expect(diffSettings(base, { ...base, session_gap_min: 30, ignored_words: ["x"] })).toEqual({
      session_gap_min: 30,
      ignored_words: ["x"],
    });
  });
  it("compares nested values structurally", () => {
    const a = { ...base, stopwords_added: { en: ["a"], sk: ["b"] } };
    const b = { ...base, stopwords_added: { sk: ["b"], en: ["a"] } };
    expect(diffSettings(a, b)).toEqual({});
    expect(diffSettings(a, { ...b, stopwords_added: { en: ["a"] } })).toEqual({
      stopwords_added: { en: ["a"] },
    });
  });
  it("sends null when a value is cleared", () => {
    expect(diffSettings({ ...base, timezone: "UTC" }, { ...base, timezone: null })).toEqual({
      timezone: null,
    });
  });
  it("flags derived fields", () => {
    expect(touchesDerived({ session_gap_min: 5 })).toBe(true);
    expect(touchesDerived({ theme: "dark", anonymize: true })).toBe(false);
  });
});

describe("helpers", () => {
  it("deepEqual treats null and undefined alike", () => {
    expect(deepEqual(null, undefined)).toBe(true);
    expect(deepEqual([1, { a: 2 }], [1, { a: 2 }])).toBe(true);
    expect(deepEqual([1], [1, 2])).toBe(false);
  });
  it("parseLines trims, dedupes and optionally splits commas", () => {
    expect(parseLines(" a \n\nb\na\n")).toEqual(["a", "b"]);
    expect(parseLines("a, b\nc", { commas: true })).toEqual(["a", "b", "c"]);
    expect(parseLines("a, b")).toEqual(["a, b"]);
  });
  it("setLangList sets and removes", () => {
    expect(setLangList({ en: ["x"] }, "sk", ["y"])).toEqual({ en: ["x"], sk: ["y"] });
    expect(setLangList({ en: ["x"] }, "en", [])).toEqual({});
  });
  it("detects invalid regexes and redacted values", () => {
    expect(invalidPatterns(["lol+", "(", "😂"])).toEqual(["("]);
    expect(isRedactedValue([{ names: ["•••", "•••"] }])).toBe(true);
    expect(isRedactedValue([{ names: ["Jana"] }])).toBe(false);
  });
  it("formats bytes and export links", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(exportHref("all", { tz: "UTC", start: undefined }, true)).toBe(
      "/api/export/all.json?tz=UTC&include_messages=true",
    );
    expect(exportHref("chat-3", {}, false)).toBe("/api/export/chat-3.json");
  });
});
