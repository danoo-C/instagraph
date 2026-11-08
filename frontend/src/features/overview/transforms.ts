/**
 * Pure API response → ECharts option transforms for the Overview (§7.1).
 * Kept free of React so they can be unit-tested.
 */
import type { EChartsOption } from "@/charts/registerEcharts";
import { defaultGrid, timeSeriesZoom } from "@/charts/sampling";
import { chatColor, seriesColor } from "@/features/overview/query";
import type {
  ApiCalendar,
  ApiCategoryChart,
  ApiChatRef,
  ApiContactItem,
  ApiEmojiCount,
  ApiGraph,
  ApiHeatmap,
  ApiTimeSeries,
  Bucket,
} from "@/features/overview/types";
import { OTHERS_COLOR, personColor } from "@/lib/colors";
import { escapeHtml } from "@/lib/utils";
import {
  formatCompact,
  formatDate,
  formatDuration,
  formatHour,
  formatMonth,
  formatNumber,
  formatPercent,
} from "@/lib/format";

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- ECharts tooltip params are untyped
type TooltipParams = any;

/** Label of a bucket start date. */
export function formatBucket(x: string, bucket: Bucket): string {
  switch (bucket) {
    case "year":
      return x.slice(0, 4);
    case "month":
      return formatMonth(x);
    default:
      return formatDate(x);
  }
}

/** Milliseconds → "3 h 12 min". */
export const formatMs = (ms: number | null | undefined) =>
  ms == null ? formatDuration(null) : formatDuration(ms / 1000);

function categoryXAxis(ts: ApiTimeSeries) {
  return {
    type: "category" as const,
    data: ts.x,
    boundaryGap: false,
    axisLabel: { formatter: (v: string) => formatBucket(v, ts.bucket), hideOverlap: true },
  };
}

const countAxis = {
  type: "value" as const,
  axisLabel: { formatter: (v: number) => formatCompact(v) },
};

// ------------------------------------------------------------------ 1. timeline (stacked area)

export function timelineOption(ts: ApiTimeSeries): EChartsOption {
  return {
    grid: defaultGrid({ slider: true, legend: true }),
    legend: { type: "scroll", top: 0 },
    tooltip: { trigger: "axis", valueFormatter: (v) => formatNumber(v as number) },
    dataZoom: timeSeriesZoom(),
    xAxis: categoryXAxis(ts),
    yAxis: countAxis,
    series: ts.series.map((s) => ({
      type: "line",
      name: s.name,
      id: s.key,
      stack: "total",
      areaStyle: { opacity: 0.85 },
      lineStyle: { width: 0.5 },
      showSymbol: false,
      symbol: "none",
      emphasis: { focus: "series" },
      color: seriesColor(s),
      data: s.values,
    })),
  };
}

// ------------------------------------------------------------------ 2. sent vs received (mirrored)

export function sentReceivedOption(ts: ApiTimeSeries): EChartsOption {
  const sent = ts.series.find((s) => s.key === "sent");
  const received = ts.series.find((s) => s.key === "received");
  const series = [];
  if (sent)
    series.push({
      type: "bar" as const,
      name: sent.name,
      id: "sent",
      stack: "mirror",
      color: personColor(0),
      data: sent.values,
    });
  if (received)
    series.push({
      type: "bar" as const,
      name: received.name,
      id: "received",
      stack: "mirror",
      color: OTHERS_COLOR,
      data: received.values.map((v) => (v == null ? null : -v)),
    });
  return {
    grid: defaultGrid({ slider: true, legend: true }),
    legend: { top: 0 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      valueFormatter: (v) => formatNumber(Math.abs(v as number)),
    },
    dataZoom: timeSeriesZoom(),
    xAxis: { ...categoryXAxis(ts), boundaryGap: true },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => formatCompact(Math.abs(v)) } },
    series,
  };
}

// ------------------------------------------------------------------ 3. contacts leaderboard

export function contactsOption(
  items: ApiContactItem[],
  format: (v: number) => string = (v) => formatNumber(v),
): EChartsOption {
  return {
    grid: { left: 8, right: 56, top: 8, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "item",
      formatter: (p: TooltipParams) => {
        const it = items[p.dataIndex as number];
        return it
          ? `${escapeHtml(it.chat.title)}<br/>${format(it.value)} · ${formatPercent(it.share)}`
          : "";
      },
    },
    xAxis: { type: "value", axisLabel: { formatter: (v: number) => format(v) } },
    yAxis: {
      type: "category",
      inverse: true,
      data: items.map((i) => i.chat.title),
      axisLabel: { width: 140, overflow: "truncate" },
    },
    series: [
      {
        type: "bar",
        data: items.map((i) => ({ value: i.value, itemStyle: { color: chatColor(i.chat) } })),
        label: { show: true, position: "right", formatter: (p: TooltipParams) => format(p.value) },
      },
    ],
  };
}

// ------------------------------------------------------------------ 4. top over time

/** Ranks greater than `maxRank` are hidden (null) so the bump chart stays readable. */
export function bumpOption(ranks: ApiTimeSeries, chats: ApiChatRef[], maxRank = 15): EChartsOption {
  const byKey = new Map(chats.map((c) => [String(c.thread_id), c]));
  return {
    grid: defaultGrid({ slider: true, legend: true }),
    legend: { type: "scroll", top: 0 },
    tooltip: {
      trigger: "item",
      formatter: (p: TooltipParams) =>
        `${escapeHtml(p.seriesName)}<br/>${formatMonth(ranks.x[p.dataIndex as number])}: #${p.value}`,
    },
    dataZoom: timeSeriesZoom(),
    xAxis: categoryXAxis(ranks),
    yAxis: {
      type: "value",
      inverse: true,
      min: 1,
      max: maxRank,
      interval: 1,
      axisLabel: { formatter: (v: number) => `#${v}` },
    },
    series: ranks.series.map((s) => {
      const chat = byKey.get(s.key);
      return {
        type: "line" as const,
        name: s.name,
        id: s.key,
        smooth: 0.3,
        connectNulls: false,
        symbolSize: 6,
        emphasis: { focus: "series" as const },
        color: chat ? chatColor(chat) : seriesColor(s),
        data: s.values.map((v) => (v == null || v > maxRank ? null : v)),
      };
    }),
  };
}

export function streamOption(stream: ApiTimeSeries, chats: ApiChatRef[]): EChartsOption {
  const byKey = new Map(chats.map((c) => [String(c.thread_id), c]));
  const data: Array<[string, number, string]> = [];
  for (const s of stream.series)
    stream.x.forEach((x, i) => data.push([x, s.values[i] ?? 0, s.name]));
  return {
    legend: { type: "scroll", top: 0, data: stream.series.map((s) => s.name) },
    tooltip: { trigger: "axis", axisPointer: { type: "line" } },
    color: stream.series.map((s) => {
      const chat = byKey.get(s.key);
      return chat ? chatColor(chat) : seriesColor(s);
    }),
    singleAxis: {
      type: "time",
      top: 40,
      bottom: 30,
      left: 24,
      right: 24,
      axisLabel: { hideOverlap: true },
    },
    series: [{ type: "themeRiver", data, emphasis: { focus: "series" }, label: { show: false } }],
  };
}

// ------------------------------------------------------------------ 5. calendar (one row per year)

export const CALENDAR_ROW_HEIGHT = 150;

export function calendarOption(
  cal: ApiCalendar,
  years: number[],
  ramp: readonly string[],
): EChartsOption {
  const sorted = [...years].sort((a, b) => b - a);
  const byYear = new Map<number, Array<[string, number]>>();
  for (const d of cal.days) {
    const y = Number(d.date.slice(0, 4));
    const list = byYear.get(y) ?? [];
    list.push([d.date, d.value]);
    byYear.set(y, list);
  }
  return {
    tooltip: {
      formatter: (p: TooltipParams) =>
        `${formatDate(p.value[0])}: ${formatNumber(p.value[1] as number)}`,
    },
    visualMap: {
      min: 0,
      max: Math.max(1, cal.max_value),
      type: "piecewise",
      splitNumber: 5,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      inRange: { color: ramp.slice(1) },
      itemWidth: 12,
      itemHeight: 12,
      seriesIndex: sorted.map((_, i) => i),
    },
    calendar: sorted.map((y, i) => ({
      range: String(y),
      top: 24 + i * CALENDAR_ROW_HEIGHT,
      left: 48,
      right: 16,
      cellSize: ["auto", 13],
      yearLabel: { show: true, position: "left" },
      dayLabel: { firstDay: 1, nameMap: "en", fontSize: 9 },
      monthLabel: { fontSize: 10 },
    })),
    series: sorted.map((y, i) => ({
      type: "heatmap" as const,
      coordinateSystem: "calendar" as const,
      calendarIndex: i,
      data: byYear.get(y) ?? [],
    })),
  };
}

export function calendarHeight(years: number): number {
  return Math.max(1, years) * CALENDAR_ROW_HEIGHT + 56;
}

// ------------------------------------------------------------------ 6. hour × weekday punch card

/** Rows = weekdays (y), columns = hours (x). Point = [hourIndex, weekdayIndex, value]. */
export function punchCardData(hm: ApiHeatmap): Array<[number, number, number]> {
  const out: Array<[number, number, number]> = [];
  hm.values.forEach((row, y) => row.forEach((v, x) => out.push([x, y, v])));
  return out;
}

export function punchCardOption(hm: ApiHeatmap, color: string): EChartsOption {
  const data = punchCardData(hm);
  const max = Math.max(1, ...data.map((d) => d[2]));
  return {
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      formatter: (p: TooltipParams) =>
        `${escapeHtml(hm.y_labels[p.value[1]])} ${formatHour(Number(hm.x_labels[p.value[0]]))}: ${formatNumber(p.value[2])}`,
    },
    xAxis: {
      type: "category",
      data: hm.x_labels,
      boundaryGap: true,
      splitLine: { show: true },
      axisLabel: { formatter: (v: string) => v.padStart(2, "0") },
    },
    yAxis: { type: "category", data: hm.y_labels, inverse: true, splitLine: { show: true } },
    series: [
      {
        type: "scatter",
        data,
        color,
        symbolSize: (v: number[]) => 4 + Math.sqrt((v[2] ?? 0) / max) * 22,
        itemStyle: { opacity: 0.85 },
      },
    ],
  };
}

// ------------------------------------------------------------------ 7. hours (radial clock)

export function hoursOption(chart: ApiCategoryChart): EChartsOption {
  return {
    legend: { top: 0 },
    tooltip: { trigger: "axis", valueFormatter: (v) => formatNumber(v as number) },
    polar: { radius: ["12%", "78%"], center: ["50%", "55%"] },
    angleAxis: {
      type: "category",
      data: chart.categories,
      startAngle: 90 + 360 / 48,
      clockwise: true,
      axisLabel: { formatter: (v: string) => v.padStart(2, "0") },
    },
    radiusAxis: { axisLabel: { show: false }, splitLine: { show: false } },
    series: chart.series.map((s) => ({
      type: "bar" as const,
      coordinateSystem: "polar" as const,
      name: s.name,
      id: s.key,
      stack: "hours",
      color: s.key === "me" ? personColor(0) : seriesColor(s),
      data: s.values,
    })),
  };
}

// ------------------------------------------------------------------ 8. weekdays

export function weekdaysOption(chart: ApiCategoryChart): EChartsOption {
  const series = chart.series.filter((s) => s.key !== "total");
  return {
    grid: defaultGrid({ legend: true }),
    legend: { top: 0 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      valueFormatter: (v) => formatNumber(v as number),
    },
    xAxis: { type: "category", data: chart.categories },
    yAxis: countAxis,
    series: (series.length ? series : chart.series).map((s) => ({
      type: "bar" as const,
      name: s.name,
      id: s.key,
      stack: "weekday",
      color: s.key === "me" ? personColor(0) : seriesColor(s),
      data: s.values,
    })),
  };
}

// ------------------------------------------------------------------ 9–11. simple series

export function lineOption(
  ts: ApiTimeSeries,
  opts: { area?: boolean; zoom?: boolean; bar?: boolean; valueFormat?: (v: number) => string } = {},
): EChartsOption {
  const fmt = opts.valueFormat ?? ((v: number) => formatNumber(v));
  const zoom = opts.zoom ?? true;
  return {
    grid: defaultGrid({ slider: zoom, legend: ts.series.length > 1 }),
    legend: ts.series.length > 1 ? { top: 0 } : undefined,
    tooltip: {
      trigger: "axis",
      valueFormatter: (v) => (v == null ? "—" : fmt(v as number)),
    },
    dataZoom: zoom ? timeSeriesZoom() : undefined,
    xAxis: { ...categoryXAxis(ts), boundaryGap: Boolean(opts.bar) },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => fmt(v) } },
    series: ts.series.map((s, i) => ({
      type: opts.bar ? ("bar" as const) : ("line" as const),
      name: s.name,
      id: s.key,
      showSymbol: false,
      connectNulls: true,
      color: s.key === "me" ? personColor(0) : i === 0 ? personColor(0) : seriesColor(s),
      areaStyle: opts.area ? { opacity: 0.2 } : undefined,
      data: s.values,
    })),
  };
}

// ------------------------------------------------------------------ 12. types (100% stacked area)

/** Convert per-type counts to shares (0..1) of each bucket's total. Empty buckets → 0. */
export function toPercentStack(ts: ApiTimeSeries): number[][] {
  const totals = ts.x.map((_, i) => ts.series.reduce((a, s) => a + (s.values[i] ?? 0), 0));
  return ts.series.map((s) => s.values.map((v, i) => (totals[i] ? (v ?? 0) / totals[i]! : 0)));
}

export function typesOption(ts: ApiTimeSeries): EChartsOption {
  const shares = toPercentStack(ts);
  return {
    grid: defaultGrid({ slider: true, legend: true }),
    legend: { type: "scroll", top: 0 },
    tooltip: { trigger: "axis", valueFormatter: (v) => formatPercent(v as number) },
    dataZoom: timeSeriesZoom(),
    xAxis: categoryXAxis(ts),
    yAxis: {
      type: "value",
      max: 1,
      axisLabel: { formatter: (v: number) => formatPercent(v, 0) },
    },
    series: ts.series.map((s, i) => ({
      type: "line" as const,
      name: s.name,
      id: s.key,
      stack: "types",
      areaStyle: { opacity: 0.85 },
      lineStyle: { width: 0.5 },
      showSymbol: false,
      symbol: "none",
      emphasis: { focus: "series" as const },
      data: shares[i],
    })),
  };
}

// ------------------------------------------------------------------ 13. emojis

export function emojiBarsOption(
  panels: Array<{ title: string; items: ApiEmojiCount[]; color: string }>,
  limit = 15,
): EChartsOption {
  const n = panels.length || 1;
  const width = 100 / n;
  return {
    title: panels.map((p, i) => ({
      text: p.title,
      left: `${i * width + 1}%`,
      top: 0,
      textStyle: { fontSize: 12, fontWeight: 500 },
    })),
    tooltip: { trigger: "item", valueFormatter: (v) => formatNumber(v as number) },
    grid: panels.map((_, i) => ({
      left: `${i * width + 1}%`,
      width: `${width - 4}%`,
      top: 28,
      bottom: 8,
      containLabel: true,
    })),
    xAxis: panels.map((_, i) => ({
      type: "value" as const,
      gridIndex: i,
      axisLabel: { formatter: (v: number) => formatCompact(v), hideOverlap: true },
    })),
    yAxis: panels.map((p, i) => ({
      type: "category" as const,
      gridIndex: i,
      inverse: true,
      data: p.items.slice(0, limit).map((e) => e.emoji),
      axisLabel: { fontSize: 16 },
    })),
    series: panels.map((p, i) => ({
      type: "bar" as const,
      name: p.title,
      xAxisIndex: i,
      yAxisIndex: i,
      color: p.color,
      data: p.items.slice(0, limit).map((e) => e.count),
    })),
  };
}

// ------------------------------------------------------------------ 14. size distribution

export function sizeOption(chart: ApiCategoryChart): EChartsOption {
  const s = chart.series[0];
  return {
    grid: { left: 8, right: 16, top: 16, bottom: 24, containLabel: true },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      valueFormatter: (v) => `${formatNumber(v as number)} chats`,
    },
    xAxis: {
      type: "category",
      data: chart.categories,
      name: "messages per chat",
      nameLocation: "middle",
      nameGap: 28,
    },
    yAxis: countAxis,
    series: s
      ? [{ type: "bar", name: s.name, color: personColor(0), barCategoryGap: "8%", data: s.values }]
      : [],
  };
}

// ------------------------------------------------------------------ 15. reply times

export function replyTimesOption(ts: ApiTimeSeries): EChartsOption {
  return lineOption(ts, { valueFormat: (v) => formatMs(v) });
}

// ------------------------------------------------------------------ 16. social graph

export function graphOption(g: ApiGraph): EChartsOption {
  const max = Math.max(1, ...g.nodes.map((n) => n.messages));
  const maxW = Math.max(1, ...g.edges.map((e) => e.weight));
  const names = new Map(g.nodes.map((n) => [n.person.person_id, n.person.name]));
  return {
    tooltip: {
      formatter: (p: TooltipParams) => {
        if (p.dataType === "edge")
          return `${escapeHtml(p.data.sourceName)} – ${escapeHtml(p.data.targetName)}<br/>${formatNumber(p.data.weight)} shared group chats`;
        return `${escapeHtml(p.data.name)}<br/>${formatNumber(p.data.messages)} messages · ${formatNumber(p.data.groupChats)} group chats`;
      },
    },
    series: [
      {
        type: "graph",
        layout: "force",
        roam: true,
        draggable: true,
        force: { repulsion: 120, edgeLength: [40, 140], gravity: 0.08 },
        label: { show: true, position: "right", fontSize: 10 },
        labelLayout: { hideOverlap: true },
        emphasis: { focus: "adjacency", lineStyle: { width: 3 } },
        lineStyle: { opacity: 0.4, curveness: 0.1 },
        data: g.nodes.map((n) => ({
          id: String(n.person.person_id),
          name: n.person.name,
          messages: n.messages,
          groupChats: n.group_chats,
          symbolSize: 6 + Math.sqrt(n.messages / max) * 34,
          itemStyle: {
            color: personColor({ id: n.person.person_id, color_index: n.person.color_index }),
          },
        })),
        links: g.edges.map((e) => ({
          source: String(e.source),
          target: String(e.target),
          weight: e.weight,
          sourceName: names.get(e.source) ?? "",
          targetName: names.get(e.target) ?? "",
          lineStyle: { width: 0.5 + (e.weight / maxW) * 4 },
        })),
      },
    ],
  };
}
