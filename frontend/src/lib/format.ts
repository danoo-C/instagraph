/**
 * Locale-aware formatting helpers. All accept an optional `locale`
 * (defaults to the browser locale) so tests can pin "en-US".
 */
type Locale = string | undefined;
type DateInput = string | number | Date | null | undefined;

const DASH = "—";

function toDate(v: DateInput): Date | null {
  if (v == null || v === "") return null;
  if (v instanceof Date) return Number.isNaN(v.getTime()) ? null : v;
  // Plain "YYYY-MM-DD" → local date (not UTC midnight).
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v)) {
    const [y, m, d] = v.split("-").map(Number) as [number, number, number];
    return new Date(y, m - 1, d);
  }
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** 12345 → "12,345". */
export function formatNumber(
  n: number | null | undefined,
  opts: Intl.NumberFormatOptions = {},
  locale?: Locale,
): string {
  if (n == null || Number.isNaN(n)) return DASH;
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 1, ...opts }).format(n);
}

/** 1234567 → "1.2M". */
export function formatCompact(n: number | null | undefined, locale?: Locale): string {
  if (n == null || Number.isNaN(n)) return DASH;
  return new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

/** Ratio 0..1 → "42.5%". */
export function formatPercent(
  ratio: number | null | undefined,
  digits = 1,
  locale?: Locale,
): string {
  if (ratio == null || Number.isNaN(ratio)) return DASH;
  return new Intl.NumberFormat(locale, {
    style: "percent",
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  }).format(ratio);
}

/**
 * Duration in seconds → short human string, two most significant units:
 * 11520 → "3 h 12 min", 45 → "45 s", 190000 → "2 d 4 h", 0.4 → "< 1 s".
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return DASH;
  const neg = seconds < 0;
  let s = Math.round(Math.abs(seconds));
  if (s === 0) return Math.abs(seconds) > 0 ? "< 1 s" : "0 s";
  const units: Array<[string, number]> = [
    ["y", 365 * 86400],
    ["d", 86400],
    ["h", 3600],
    ["min", 60],
    ["s", 1],
  ];
  const parts: string[] = [];
  for (const [label, size] of units) {
    if (s >= size) {
      const v = Math.floor(s / size);
      s -= v * size;
      parts.push(`${v} ${label}`);
    } else if (parts.length > 0) {
      parts.push(""); // keep position so we stop after 2 adjacent slots
    }
    if (parts.length === 2) break;
  }
  const out = parts.filter(Boolean).join(" ");
  return neg ? `-${out}` : out;
}

/** Date → "Mar 5, 2024" (medium) or custom style. */
export function formatDate(
  v: DateInput,
  opts: Intl.DateTimeFormatOptions = { dateStyle: "medium" },
  locale?: Locale,
): string {
  const d = toDate(v);
  return d ? new Intl.DateTimeFormat(locale, opts).format(d) : DASH;
}

/** Date+time → "Mar 5, 2024, 14:03". Pass `timeZone` to render in the filter tz. */
export function formatDateTime(v: DateInput, timeZone?: string, locale?: Locale): string {
  const d = toDate(v);
  if (!d) return DASH;
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "short",
    ...(timeZone ? { timeZone } : {}),
  }).format(d);
}

/** "Mar 2024". */
export function formatMonth(v: DateInput, locale?: Locale): string {
  return formatDate(v, { month: "short", year: "numeric" }, locale);
}

/** Range label for exports and headers: "Jan 1, 2023 – Dec 31, 2023", "All time". */
export function formatDateRange(
  start: DateInput,
  end: DateInput,
  locale?: Locale,
  allTimeLabel = "All time",
): string {
  const s = toDate(start);
  const e = toDate(end);
  if (!s && !e) return allTimeLabel;
  if (s && e) {
    return new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).formatRange(s, e);
  }
  return s ? `${formatDate(s, undefined, locale)} –` : `– ${formatDate(e, undefined, locale)}`;
}

/** Hour 0..23 → "14:00" (locale aware). */
export function formatHour(hour: number, locale?: Locale): string {
  return new Intl.DateTimeFormat(locale, { hour: "numeric", minute: "2-digit" }).format(
    new Date(2000, 0, 1, hour, 0),
  );
}

/** Weekday index (0 = Monday … 6 = Sunday, ISO) → "Mon". */
export function formatWeekday(
  isoIndex: number,
  style: "short" | "long" = "short",
  locale?: Locale,
): string {
  // 2024-01-01 was a Monday.
  return new Intl.DateTimeFormat(locale, { weekday: style }).format(
    new Date(2024, 0, 1 + isoIndex),
  );
}

export type ValueFormat = "number" | "compact" | "percent" | "duration" | "text";

/** Format a tile/table value by kind (percent expects 0..1, duration seconds). */
export function formatValue(
  value: number | string | null | undefined,
  format: ValueFormat = "number",
  locale?: Locale,
): string {
  if (value == null) return DASH;
  if (typeof value === "string") return value;
  switch (format) {
    case "compact":
      return formatCompact(value, locale);
    case "percent":
      return formatPercent(value, 1, locale);
    case "duration":
      return formatDuration(value);
    case "text":
      return String(value);
    default:
      return formatNumber(value, {}, locale);
  }
}
