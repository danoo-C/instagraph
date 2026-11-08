import { OTHERS_COLOR, PERSON_PALETTE, SEQUENTIAL_DARK, SEQUENTIAL_LIGHT } from "@/lib/colors";
import type { ResolvedTheme } from "@/state/theme";

/** Raw color tokens per theme (mirror of the CSS variables in index.css). */
export const CHART_TOKENS = {
  light: {
    background: "#ffffff",
    text: "#0f172a",
    muted: "#64748b",
    axis: "#cbd5e1",
    grid: "#e2e8f0",
    tooltipBg: "#ffffff",
    tooltipBorder: "#e2e8f0",
    sequential: SEQUENTIAL_LIGHT,
  },
  dark: {
    background: "#0f172a",
    text: "#e2e8f0",
    muted: "#94a3b8",
    axis: "#334155",
    grid: "#1e293b",
    tooltipBg: "#1e293b",
    tooltipBorder: "#334155",
    sequential: SEQUENTIAL_DARK,
  },
} as const;

export type ChartTokens = (typeof CHART_TOKENS)[ResolvedTheme];

export const chartThemeName = (t: ResolvedTheme) => `instagraph-${t}`;

function axis(tk: ChartTokens) {
  return {
    axisLine: { show: true, lineStyle: { color: tk.axis } },
    axisTick: { show: false },
    axisLabel: { color: tk.muted },
    splitLine: { show: true, lineStyle: { color: tk.grid } },
    splitArea: { show: false },
    nameTextStyle: { color: tk.muted },
  };
}

/** ECharts theme object (registered once per theme in registerEcharts.ts). */
export function buildEchartsTheme(t: ResolvedTheme): Record<string, unknown> {
  const tk = CHART_TOKENS[t];
  return {
    color: [...PERSON_PALETTE, OTHERS_COLOR],
    backgroundColor: "transparent",
    textStyle: {
      color: tk.text,
      fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
    },
    title: { textStyle: { color: tk.text }, subtextStyle: { color: tk.muted } },
    legend: { textStyle: { color: tk.muted }, pageTextStyle: { color: tk.muted } },
    tooltip: {
      backgroundColor: tk.tooltipBg,
      borderColor: tk.tooltipBorder,
      textStyle: { color: tk.text },
      axisPointer: { lineStyle: { color: tk.axis }, crossStyle: { color: tk.axis } },
    },
    categoryAxis: { ...axis(tk), splitLine: { show: false } },
    valueAxis: { ...axis(tk), axisLine: { show: false } },
    timeAxis: { ...axis(tk), splitLine: { show: false } },
    logAxis: axis(tk),
    visualMap: { textStyle: { color: tk.muted }, inRange: { color: [...tk.sequential] } },
    dataZoom: {
      textStyle: { color: tk.muted },
      borderColor: tk.axis,
      fillerColor: t === "dark" ? "rgba(148,163,184,0.15)" : "rgba(100,116,139,0.12)",
      handleStyle: { color: tk.background, borderColor: tk.muted },
      dataBackground: { lineStyle: { color: tk.axis }, areaStyle: { color: tk.grid } },
    },
    calendar: {
      itemStyle: { color: "transparent", borderColor: tk.background, borderWidth: 2 },
      splitLine: { lineStyle: { color: tk.axis } },
      dayLabel: { color: tk.muted },
      monthLabel: { color: tk.muted },
      yearLabel: { color: tk.muted },
    },
    graph: { lineStyle: { color: tk.axis }, label: { color: tk.text } },
    line: { symbol: "circle", symbolSize: 4, smooth: false },
    bar: { itemStyle: { borderRadius: 0 } },
  };
}
