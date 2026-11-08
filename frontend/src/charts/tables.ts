import type { CsvCell, CsvTable } from "@/charts/csv";

/** Structural shapes of the backend chart payloads (TimeSeries, CategoryChart, Heatmap). */
interface SeriesLike {
  name: string;
  values: ReadonlyArray<number | null | undefined>;
}
export interface TimeSeriesLike {
  x: readonly string[];
  series: readonly SeriesLike[];
}
export interface CategoryChartLike {
  categories: readonly string[];
  series: readonly SeriesLike[];
}
export interface HeatmapLike {
  x_labels: readonly string[];
  y_labels: readonly string[];
  values: ReadonlyArray<ReadonlyArray<number | null>>;
}

/** TimeSeries → CSV/table: one row per bucket, one column per series (null while loading). */
export function timeSeriesTable(
  ts: TimeSeriesLike | null | undefined,
  xHeader = "date",
): CsvTable | null {
  if (!ts) return null;
  return {
    headers: [xHeader, ...ts.series.map((s) => s.name)],
    rows: ts.x.map((x, i) => [x, ...ts.series.map((s) => (s.values[i] ?? null) as CsvCell)]),
  };
}

/** CategoryChart → CSV/table: one row per category, one column per series. */
export function categoryTable(
  chart: CategoryChartLike | null | undefined,
  header = "category",
): CsvTable | null {
  if (!chart) return null;
  return {
    headers: [header, ...chart.series.map((s) => s.name)],
    rows: chart.categories.map((c, i) => [
      c,
      ...chart.series.map((s) => (s.values[i] ?? null) as CsvCell),
    ]),
  };
}

/** Heatmap → CSV/table: one row per y label, one column per x label. */
export function heatmapTable(
  h: HeatmapLike | null | undefined,
  cornerHeader = "",
): CsvTable | null {
  if (!h) return null;
  return {
    headers: [cornerHeader, ...h.x_labels],
    rows: h.y_labels.map((y, j) => [y, ...(h.values[j] ?? []).map((v) => v as CsvCell)]),
  };
}
