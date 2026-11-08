import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { formatDateRange } from "@/lib/format";
import { toIsoDate } from "@/lib/utils";

/**
 * Global filters (§5.1, §8.2). The URL query string is the single source of truth:
 * shareable links, back button works. Only non-default values are written to the URL.
 */
export interface Filters {
  /** ISO date (YYYY-MM-DD), inclusive; null = unbounded. */
  start: string | null;
  /** ISO date (YYYY-MM-DD), inclusive; null = unbounded. */
  end: string | null;
  /** IANA time zone. */
  tz: string;
  include_groups: boolean;
  include_requests: boolean;
  include_archived: boolean;
  exclude_system: boolean;
  /** Person ids. */
  people: string[];
}

/** Filters serialized as backend query parameters (strings only). */
export type FilterQuery = {
  start?: string;
  end?: string;
  tz: string;
  include_groups: "true" | "false";
  include_requests: "true" | "false";
  include_archived: "true" | "false";
  exclude_system: "true" | "false";
  people?: string;
};

export const FILTER_KEYS = [
  "start",
  "end",
  "tz",
  "include_groups",
  "include_requests",
  "include_archived",
  "exclude_system",
  "people",
] as const satisfies ReadonlyArray<keyof Filters>;

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function defaultFilters(tz: string = browserTimeZone()): Filters {
  return {
    start: null,
    end: null,
    tz,
    include_groups: true,
    include_requests: false,
    include_archived: true,
    exclude_system: true,
    people: [],
  };
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const BOOL_KEYS = [
  "include_groups",
  "include_requests",
  "include_archived",
  "exclude_system",
] as const;

function parseBool(v: string | null, fallback: boolean): boolean {
  if (v === "true" || v === "1") return true;
  if (v === "false" || v === "0") return false;
  return fallback;
}

/** URL → Filters. Invalid values fall back to defaults. */
export function parseFilters(params: URLSearchParams, tz: string = browserTimeZone()): Filters {
  const d = defaultFilters(tz);
  const start = params.get("start");
  const end = params.get("end");
  const f: Filters = {
    ...d,
    start: start && ISO_DATE.test(start) ? start : null,
    end: end && ISO_DATE.test(end) ? end : null,
    tz: params.get("tz") || d.tz,
    people: (params.get("people") ?? "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  };
  for (const k of BOOL_KEYS) f[k] = parseBool(params.get(k), d[k]);
  return f;
}

/**
 * Writes filters into a copy of `base` (other, non-filter params are kept).
 * Default values are removed so URLs stay short.
 */
export function writeFilters(
  filters: Filters,
  base: URLSearchParams = new URLSearchParams(),
  tz: string = browserTimeZone(),
): URLSearchParams {
  const out = new URLSearchParams(base);
  const d = defaultFilters(tz);
  const set = (k: string, v: string | null) => (v == null ? out.delete(k) : out.set(k, v));
  set("start", filters.start);
  set("end", filters.end);
  set("tz", filters.tz === d.tz ? null : filters.tz);
  for (const k of BOOL_KEYS) set(k, filters[k] === d[k] ? null : String(filters[k]));
  set("people", filters.people.length ? filters.people.join(",") : null);
  return out;
}

/** Filters → backend query params (explicit booleans, tz always present). */
export function filtersToQuery(f: Filters): FilterQuery {
  const b = (v: boolean) => (v ? "true" : "false") as "true" | "false";
  return {
    ...(f.start ? { start: f.start } : {}),
    ...(f.end ? { end: f.end } : {}),
    tz: f.tz,
    include_groups: b(f.include_groups),
    include_requests: b(f.include_requests),
    include_archived: b(f.include_archived),
    exclude_system: b(f.exclude_system),
    ...(f.people.length ? { people: f.people.join(",") } : {}),
  };
}

// ---------------------------------------------------------------- presets

export type RangePreset =
  | { kind: "all" }
  | { kind: "year"; year: number }
  | { kind: "last12m" }
  | { kind: "last30d" }
  | { kind: "custom" };

function addDays(d: Date, n: number): Date {
  const c = new Date(d);
  c.setDate(c.getDate() + n);
  return c;
}

/** Concrete start/end for a preset relative to `today`. `custom` returns null (keep current). */
export function presetRange(
  p: RangePreset,
  today: Date = new Date(),
): { start: string | null; end: string | null } | null {
  switch (p.kind) {
    case "all":
      return { start: null, end: null };
    case "year":
      return { start: `${p.year}-01-01`, end: `${p.year}-12-31` };
    case "last12m": {
      const s = new Date(today);
      s.setFullYear(s.getFullYear() - 1);
      return { start: toIsoDate(addDays(s, 1)), end: toIsoDate(today) };
    }
    case "last30d":
      return { start: toIsoDate(addDays(today, -29)), end: toIsoDate(today) };
    case "custom":
      return null;
  }
}

/** Detect which preset (if any) matches the current start/end. */
export function detectPreset(
  f: Pick<Filters, "start" | "end">,
  today: Date = new Date(),
): RangePreset {
  if (!f.start && !f.end) return { kind: "all" };
  const ym = f.start?.match(/^(\d{4})-01-01$/);
  if (ym && f.end === `${ym[1]}-12-31`) return { kind: "year", year: Number(ym[1]) };
  for (const kind of ["last12m", "last30d"] as const) {
    const r = presetRange({ kind }, today);
    if (r && r.start === f.start && r.end === f.end) return { kind };
  }
  return { kind: "custom" };
}

export function presetToValue(p: RangePreset): string {
  return p.kind === "year" ? `year:${p.year}` : p.kind;
}

export function valueToPreset(v: string): RangePreset {
  if (v.startsWith("year:")) return { kind: "year", year: Number(v.slice(5)) };
  if (v === "last12m" || v === "last30d" || v === "custom" || v === "all") return { kind: v };
  return { kind: "all" };
}

// ---------------------------------------------------------------- hook

export interface UseFiltersResult {
  filters: Filters;
  /** Merge a partial update into the URL (push a history entry). */
  setFilters: (patch: Partial<Filters>) => void;
  /** Restore all defaults (keeps non-filter params). */
  resetFilters: () => void;
  /** Backend query params for the current filters. */
  toQuery: () => FilterQuery;
  preset: RangePreset;
  setPreset: (p: RangePreset) => void;
  /** Human label of the date range, e.g. "All time" or "Jan 1, 2023 – Dec 31, 2023". */
  rangeLabel: string;
  /** True when any filter differs from its default. */
  isDefault: boolean;
}

export function useFilters(): UseFiltersResult {
  const [params, setParams] = useSearchParams();
  const key = params.toString();
  const filters = useMemo(() => parseFilters(new URLSearchParams(key)), [key]);

  const setFilters = useCallback(
    (patch: Partial<Filters>) =>
      setParams((prev) => writeFilters({ ...parseFilters(prev), ...patch }, prev)),
    [setParams],
  );
  const resetFilters = useCallback(
    () => setParams((prev) => writeFilters(defaultFilters(), prev)),
    [setParams],
  );
  const setPreset = useCallback(
    (p: RangePreset) => {
      const r = presetRange(p);
      if (r) setFilters(r);
    },
    [setFilters],
  );

  return useMemo(() => {
    const d = defaultFilters();
    return {
      filters,
      setFilters,
      resetFilters,
      toQuery: () => filtersToQuery(filters),
      preset: detectPreset(filters),
      setPreset,
      rangeLabel: formatDateRange(filters.start, filters.end),
      isDefault: writeFilters(filters, new URLSearchParams(), d.tz).toString() === "",
    };
  }, [filters, setFilters, resetFilters, setPreset]);
}
