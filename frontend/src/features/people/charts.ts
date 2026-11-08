import type { EChartsOption } from "@/charts/registerEcharts";
import { defaultGrid, timeSeriesZoom } from "@/charts/sampling";
import type { ApiCategoryChart, ApiTimeSeries } from "@/features/people/apiTypes";
import { personColor } from "@/lib/colors";
import { formatHour, formatMonth } from "@/lib/format";

type Series = ApiTimeSeries["series"][number];
type ValueFmt = (v: number) => string;

/** Color of a backend series: person color if it has one, else by position. */
export function seriesColor(s: Series, index: number, colorIndex?: number | null): string {
  const ci = colorIndex ?? s.color_index;
  if (ci != null) return personColor(ci);
  if (s.person_id != null) return personColor(String(s.person_id));
  return personColor(index);
}

/** Overlaid line chart for a TimeSeries (one line per series). */
export function timeSeriesOption(
  ts: ApiTimeSeries,
  opts: { format?: ValueFmt; area?: boolean; colors?: (s: Series, i: number) => string } = {},
): EChartsOption {
  const fmt = opts.format ?? ((v: number) => String(v));
  return {
    grid: defaultGrid({ slider: ts.x.length > 24, legend: true }),
    legend: { top: 0, type: "scroll" },
    tooltip: {
      trigger: "axis",
      valueFormatter: (v) => (typeof v === "number" ? fmt(v) : "—"),
    },
    dataZoom: ts.x.length > 24 ? timeSeriesZoom({ slider: true }) : undefined,
    xAxis: {
      type: "category",
      data: ts.x,
      axisLabel: { formatter: (v: string) => formatMonth(v) },
    },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => fmt(v) } },
    series: ts.series.map((s, i) => ({
      type: "line",
      name: s.name,
      data: s.values,
      showSymbol: false,
      connectNulls: false,
      smooth: false,
      color: opts.colors ? opts.colors(s, i) : seriesColor(s, i),
      ...(opts.area ? { areaStyle: { opacity: 0.12 } } : {}),
    })),
  };
}

/** Grouped bar chart for a CategoryChart; hour categories ("0".."23") get hour labels. */
export function categoryOption(
  chart: ApiCategoryChart,
  opts: {
    format?: ValueFmt;
    hours?: boolean;
    line?: boolean;
    colors?: (s: Series, i: number) => string;
  } = {},
): EChartsOption {
  const fmt = opts.format ?? ((v: number) => String(v));
  const labels = opts.hours ? chart.categories.map((c) => formatHour(Number(c))) : chart.categories;
  return {
    grid: defaultGrid({ legend: true }),
    legend: { top: 0, type: "scroll" },
    tooltip: {
      trigger: "axis",
      valueFormatter: (v) => (typeof v === "number" ? fmt(v) : "—"),
    },
    xAxis: { type: "category", data: labels },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => fmt(v) } },
    series: chart.series.map((s, i) => ({
      type: opts.line ? "line" : "bar",
      name: s.name,
      data: s.values,
      showSymbol: false,
      smooth: opts.line ? 0.2 : undefined,
      color: opts.colors ? opts.colors(s, i) : seriesColor(s, i),
    })),
  };
}

/** True when a chart has no non-zero values. */
export function isEmptyChart(chart: { series: Series[] } | undefined): boolean {
  if (!chart) return true;
  return !chart.series.some((s) => s.values.some((v) => v != null && v !== 0));
}
