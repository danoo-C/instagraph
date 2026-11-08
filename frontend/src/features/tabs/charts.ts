import { colorForIndex, OTHERS_COLOR, personColor } from "@/lib/colors";
import { formatDuration, formatNumber, formatPercent } from "@/lib/format";
import type {
  CategoryChart,
  HeatmapData,
  PersonRef,
  Series,
  TimeSeries,
  Unit,
} from "@/features/tabs/types";

/**
 * Pure transforms from the generic API chart shapes (TimeSeries, CategoryChart,
 * HeatmapData) into ECharts series and CSV tables. Shared by all chat tabs.
 */

/** Color of a series: person color when it belongs to a person, else palette by position. */
export function seriesColor(s: Pick<Series, "person_id" | "color_index" | "key">, i = 0): string {
  if (s.color_index != null) return colorForIndex(s.color_index);
  if (s.person_id != null) return personColor(String(s.person_id));
  if (s.key === "others") return OTHERS_COLOR;
  return colorForIndex(i);
}

export function personRefColor(p: Pick<PersonRef, "person_id" | "color_index">): string {
  return personColor({ id: p.person_id, color_index: p.color_index });
}

/** Convert a value in `unit` to the number used on a chart axis (ms → seconds). */
export function toAxisValue(v: number | null | undefined, unit?: Unit | null): number | null {
  if (v == null) return null;
  return unit === "ms" ? v / 1000 : v;
}

/** Human formatting of a value in a backend unit. */
export function formatUnit(v: number | null | undefined, unit?: Unit | null): string {
  if (v == null) return "—";
  switch (unit) {
    case "ratio":
      return formatPercent(v);
    case "ms":
      return formatDuration(v / 1000);
    case "s":
      return formatDuration(v);
    case "per_message":
    case "per_day":
      return formatNumber(v, { maximumFractionDigits: 2 });
    default:
      return formatNumber(v);
  }
}

export interface SeriesOptions {
  type?: "line" | "bar";
  stack?: string;
  area?: boolean;
  smooth?: boolean;
  /** Override the color per series. */
  color?: (s: Series, i: number) => string;
  /** Keep only these series keys. */
  keys?: readonly string[];
}

/** TimeSeries → ECharts series using `[x, y]` pairs on a time axis. */
export function timeSeriesToEcharts(ts: TimeSeries | null | undefined, opts: SeriesOptions = {}) {
  if (!ts) return [];
  const { type = "line", stack, area, smooth, color, keys } = opts;
  return ts.series
    .filter((s) => !keys || keys.includes(s.key))
    .map((s, i) => {
      const c = color ? color(s, i) : seriesColor(s, i);
      return {
        name: s.name,
        type,
        ...(stack ? { stack } : {}),
        ...(area ? { areaStyle: { opacity: 0.7 } } : {}),
        ...(type === "line" ? { showSymbol: ts.x.length < 60, smooth: smooth ?? false } : {}),
        itemStyle: { color: c },
        lineStyle: type === "line" ? { color: c, width: 2 } : undefined,
        emphasis: { focus: "series" as const },
        data: ts.x.map((x, j) => [x, toAxisValue(s.values[j], ts.unit)]),
      };
    });
}

/** CategoryChart → ECharts category series (bar by default). */
export function categoryToEcharts(
  chart: CategoryChart | null | undefined,
  opts: SeriesOptions = {},
) {
  if (!chart) return [];
  const { type = "bar", stack, color, keys } = opts;
  return chart.series
    .filter((s) => !keys || keys.includes(s.key))
    .map((s, i) => {
      const c = color ? color(s, i) : seriesColor(s, i);
      return {
        name: s.name,
        type,
        ...(stack ? { stack } : {}),
        itemStyle: { color: c },
        emphasis: { focus: "series" as const },
        data: s.values.map((v) => toAxisValue(v, chart.unit)),
      };
    });
}

/** HeatmapData → ECharts heatmap points `[xIndex, yIndex, value]` (+ max value). */
export function heatmapPoints(h: HeatmapData | null | undefined): {
  data: [number, number, number][];
  max: number;
} {
  const data: [number, number, number][] = [];
  let max = 0;
  h?.values.forEach((row, y) =>
    row.forEach((v, x) => {
      data.push([x, y, v]);
      if (v > max) max = v;
    }),
  );
  return { data, max };
}

/** Sum of all values of a series (null = 0). */
export function seriesTotal(s: Pick<Series, "values">): number {
  return s.values.reduce<number>((a, v) => a + (v ?? 0), 0);
}

/** True when a chart has no non-zero values. */
export function isEmptyChart(
  chart: { series: Pick<Series, "values">[] } | null | undefined,
): boolean {
  return !chart || chart.series.every((s) => s.values.every((v) => !v));
}

/**
 * End date (inclusive, ISO) of the bucket that starts at `start`, for drilldowns from a
 * time-series point: day → same day, week → +6 days, month → last day, year → Dec 31.
 */
export function bucketEnd(start: string, bucket: TimeSeries["bucket"]): string {
  const [y, m, d] = start.slice(0, 10).split("-").map(Number) as [number, number, number];
  let end: Date;
  switch (bucket) {
    case "day":
      return start.slice(0, 10);
    case "week":
      end = new Date(Date.UTC(y, m - 1, d + 6));
      break;
    case "month":
      end = new Date(Date.UTC(y, m, 0));
      break;
    case "year":
      end = new Date(Date.UTC(y, 11, 31));
      break;
  }
  return end.toISOString().slice(0, 10);
}
