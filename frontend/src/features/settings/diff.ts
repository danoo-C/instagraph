import type { Schemas } from "@/features/people/apiTypes";

export type AppSettings = Schemas["AppSettings"];
export type SettingsPatch = Partial<AppSettings>;

/** Settings whose change re-derives the dataset on the backend (progress indicator). */
export const DERIVED_FIELDS = [
  "owner_person_id",
  "owner_name",
  "people_merges",
  "thread_merges",
  "thread_splits",
  "session_gap_min",
  "reply_window_h",
] as const satisfies ReadonlyArray<keyof AppSettings>;

/** Placeholder the backend sends for name-bearing fields while anonymize is on. */
export const HIDDEN = "•••";

/** Structural equality for JSON-like values (key order of objects ignored). */
export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a == null || b == null) return (a ?? null) === (b ?? null);
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((x, i) => deepEqual(x, b[i]));
  }
  if (typeof a === "object" && typeof b === "object") {
    const ka = Object.keys(a as object).filter(
      (k) => (a as Record<string, unknown>)[k] !== undefined,
    );
    const kb = Object.keys(b as object).filter(
      (k) => (b as Record<string, unknown>)[k] !== undefined,
    );
    if (ka.length !== kb.length) return false;
    return ka.every((k) =>
      deepEqual((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]),
    );
  }
  return false;
}

/** Fields of `draft` that differ from `base` (the PUT body: a partial update). */
export function diffSettings(base: AppSettings, draft: AppSettings): SettingsPatch {
  const out: Record<string, unknown> = {};
  const keys = new Set([...Object.keys(base), ...Object.keys(draft)]);
  for (const k of keys) {
    const a = (base as Record<string, unknown>)[k];
    const b = (draft as Record<string, unknown>)[k];
    if (!deepEqual(a, b)) out[k] = b === undefined ? null : b;
  }
  return out as SettingsPatch;
}

export function touchesDerived(patch: SettingsPatch): boolean {
  return DERIVED_FIELDS.some((k) => k in patch);
}

/** Textarea → list: one item per line (or comma), trimmed, empty and duplicate items dropped. */
export function parseLines(text: string, { commas = false }: { commas?: boolean } = {}): string[] {
  const parts = text.split(commas ? /[\n,]/ : /\n/);
  const out: string[] = [];
  for (const p of parts) {
    const s = p.trim();
    if (s && !out.includes(s)) out.push(s);
  }
  return out;
}

export function linesToText(list: readonly string[] | null | undefined): string {
  return (list ?? []).join("\n");
}

/** Patterns that JavaScript cannot compile (the backend uses Python `regex`; this is a hint). */
export function invalidPatterns(patterns: readonly string[]): string[] {
  return patterns.filter((p) => {
    try {
      new RegExp(p, "iu");
      return false;
    } catch {
      return true;
    }
  });
}

/** Set (or remove when empty) one language's list in a per-language dict. */
export function setLangList(
  dict: Record<string, string[]> | undefined,
  lang: string,
  list: string[],
): Record<string, string[]> {
  const next = { ...(dict ?? {}) };
  if (list.length) next[lang] = list;
  else delete next[lang];
  return next;
}

/** True if a value contains the anonymize placeholder (merge rules etc. are hidden). */
export function isRedactedValue(v: unknown): boolean {
  if (v === HIDDEN) return true;
  if (Array.isArray(v)) return v.some(isRedactedValue);
  if (v && typeof v === "object") return Object.values(v).some(isRedactedValue);
  return false;
}

/** Sorted IANA time zones (falls back to a short list on old browsers). */
export function timeZones(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (k: string) => string[] };
  try {
    const list = intl.supportedValuesOf?.("timeZone");
    if (list && list.length) return list.includes("UTC") ? list : ["UTC", ...list];
  } catch {
    // ignore
  }
  return [
    "UTC",
    "Europe/London",
    "Europe/Bratislava",
    "Europe/Prague",
    "America/New_York",
    "Asia/Tokyo",
  ];
}

/** "1.5 MB" for byte counts. */
export function formatBytes(n: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${i === 0 ? v : v.toFixed(1)} ${units[i]}`;
}
