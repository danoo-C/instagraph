import { OTHERS_COLOR, personColor } from "@/lib/colors";
import { formatDuration } from "@/lib/format";
import type { ApiSeries, Bucket, PersonReplyStats } from "@/features/chat/types";

/** Pure data transforms shared by the chat tabs (tested in charts.test.ts). */

/** Color of a backend series: person color when it has a color slot, else `fallback`. */
export function seriesColor(
  s: Pick<ApiSeries, "color_index" | "person_id" | "key">,
  fallback?: string,
) {
  if (s.color_index != null) return personColor(s.color_index);
  if (s.person_id != null) return personColor(String(s.person_id));
  return fallback ?? OTHERS_COLOR;
}

/** Milliseconds → human duration ("3 h 12 min"). */
export function formatMs(ms: number | null | undefined): string {
  return formatDuration(ms == null ? null : ms / 1000);
}

/** Heatmap → ECharts heatmap data `[x, y, value]`. */
export function heatmapPoints(values: number[][]): Array<[number, number, number]> {
  const out: Array<[number, number, number]> = [];
  values.forEach((row, y) => row.forEach((v, x) => out.push([x, y, v])));
  return out;
}

/** Each cell divided by the matrix total (0 when empty). */
export function normalizeMatrix(values: number[][]): number[][] {
  const total = values.reduce((a, row) => a + row.reduce((b, v) => b + v, 0), 0);
  return values.map((row) => row.map((v) => (total ? v / total : 0)));
}

/**
 * Difference of two hour×weekday matrices after normalizing each to shares of its own
 * total, so different message volumes don't dominate (values in -1..1, i.e. share points).
 */
export function diffMatrix(a: number[][], b: number[][]): number[][] {
  const na = normalizeMatrix(a);
  const nb = normalizeMatrix(b);
  return na.map((row, y) => row.map((v, x) => v - (nb[y]?.[x] ?? 0)));
}

/** Largest absolute value of a matrix (for symmetric diverging scales). */
export function maxAbs(values: number[][]): number {
  let m = 0;
  for (const row of values) for (const v of row) m = Math.max(m, Math.abs(v));
  return m;
}

/** Add `days` to an ISO date (YYYY-MM-DD), in UTC to avoid DST issues. */
export function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** Inclusive [start, end] dates of the bucket that starts at `start`. */
export function bucketRange(start: string, bucket: Bucket): { start: string; end: string } {
  const [y, m] = start.split("-").map(Number) as [number, number];
  switch (bucket) {
    case "day":
      return { start, end: start };
    case "week":
      return { start, end: addDays(start, 6) };
    case "month": {
      const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
      return { start, end: `${start.slice(0, 7)}-${String(last).padStart(2, "0")}` };
    }
    case "year":
      return { start, end: `${y}-12-31` };
  }
}

/**
 * Box plot rows `[low, q1, median, q3, high]` in seconds per person from reply stats.
 * Whiskers: fastest reply … p90 (the backend has no p10/max-within-window). Null when
 * the person has no replies.
 */
export function replyBoxRows(
  replies: PersonReplyStats[],
): Array<[number, number, number, number, number] | null> {
  const s = (v: number | null | undefined) => Math.max((v ?? 0) / 1000, 0.5);
  return replies.map((r) =>
    r.median_ms == null || r.replies === 0
      ? null
      : [s(r.fastest_ms ?? r.p25_ms), s(r.p25_ms), s(r.median_ms), s(r.p75_ms), s(r.p90_ms)],
  );
}

/** Values converted with `fn`, keeping nulls. */
export function mapValues(values: Array<number | null>, fn: (v: number) => number) {
  return values.map((v) => (v == null ? null : fn(v)));
}

/** Short duration label for log time axes (seconds): 1 s, 1 min, 1 h, 12 h. */
export function shortSeconds(sec: number): string {
  if (sec < 60) return `${Math.round(sec)} s`;
  if (sec < 3600) return `${Math.round(sec / 60)} min`;
  if (sec < 86400) return `${Math.round((sec / 3600) * 10) / 10} h`;
  return `${Math.round((sec / 86400) * 10) / 10} d`;
}

export type TrendLabel = "rising" | "stable" | "fading";

/** Arrow + accessible label for a chat trend (§6.9). */
export function trendArrow(trend: TrendLabel): {
  symbol: string;
  label: string;
  className: string;
} {
  switch (trend) {
    case "rising":
      return { symbol: "↗", label: "Rising", className: "text-emerald-600 dark:text-emerald-400" };
    case "fading":
      return { symbol: "↘", label: "Fading", className: "text-danger" };
    default:
      return { symbol: "→", label: "Stable", className: "text-muted" };
  }
}

/** SVG polyline points for a sparkline of `values` in a `w`×`h` box (1px padding). */
export function sparklinePoints(values: number[], w: number, h: number): string {
  if (values.length === 0) return "";
  const max = Math.max(...values, 0);
  const n = values.length;
  const step = n > 1 ? (w - 2) / (n - 1) : 0;
  return values
    .map((v, i) => {
      const x = 1 + (n > 1 ? i * step : (w - 2) / 2);
      const y = max > 0 ? h - 1 - (v / max) * (h - 2) : h - 1;
      return `${round2(x)},${round2(y)}`;
    })
    .join(" ");
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

/** Effort radar: per-part min/max over people (z-scores), padded so shapes don't touch edges. */
export function radarIndicators(
  parts: Array<{ key: string; label: string }>,
  zs: Array<Array<number | null>>,
): Array<{ name: string; min: number; max: number }> {
  let lim = 1;
  for (const row of zs) for (const z of row) if (z != null) lim = Math.max(lim, Math.abs(z));
  lim = Math.ceil(lim * 1.2 * 10) / 10;
  return parts.map((p) => ({ name: p.label, min: -lim, max: lim }));
}
