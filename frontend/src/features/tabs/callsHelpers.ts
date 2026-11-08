import { escapeHtml } from "@/lib/utils";
import type { EChartsOption } from "@/charts/registerEcharts";
import { CHART_TOKENS } from "@/charts/theme";
import { formatDateTime } from "@/lib/format";
import { formatUnit, heatmapPoints } from "@/features/tabs/charts";
import type { ApiTile, HeatmapData, PersonRef } from "@/features/tabs/types";
import type { ResolvedTheme } from "@/state/theme";

/** Shared helpers for the Emojis / Reactions / Calls tabs. */

/** Display string for an API tile value, honouring its unit. */
export function apiTileValue(t: Pick<ApiTile, "value" | "unit">, timeZone?: string): string {
  const v = t.value;
  if (v == null) return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "string") {
    if (t.unit === "datetime") return formatDateTime(v, timeZone);
    return v;
  }
  return formatUnit(v, t.unit);
}

/** Find a person by display name (CategoryChart categories are names). */
export function personByName(
  people: readonly PersonRef[] | undefined,
  name: string,
): PersonRef | undefined {
  return people?.find((p) => p.name === name);
}

/** ECharts option for a HeatmapData (x × y grid, sequential ramp). */
export function heatmapOption(
  h: HeatmapData,
  theme: ResolvedTheme,
  opts: { xName?: string; yName?: string; valueLabel?: string } = {},
): EChartsOption {
  const { data, max } = heatmapPoints(h);
  return {
    grid: { left: 8, right: 16, top: 16, bottom: 56, containLabel: true },
    tooltip: {
      position: "top",
      formatter: (p: unknown) => {
        const v = (p as { value: [number, number, number] }).value;
        return `${escapeHtml(h.y_labels[v[1]] ?? "")} · ${escapeHtml(h.x_labels[v[0]] ?? "")}<br/>${escapeHtml(opts.valueLabel ?? "Value")}: ${v[2]}`;
      },
    },
    xAxis: {
      type: "category",
      data: h.x_labels,
      name: opts.xName,
      nameLocation: "middle",
      nameGap: 28,
      splitArea: { show: true },
    },
    yAxis: { type: "category", data: h.y_labels, name: opts.yName, inverse: true },
    visualMap: {
      min: 0,
      max: Math.max(1, max),
      calculable: true,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      itemHeight: 120,
      inRange: { color: [...CHART_TOKENS[theme].sequential] },
    },
    series: [
      {
        type: "heatmap",
        data,
        emphasis: { itemStyle: { borderColor: CHART_TOKENS[theme].text, borderWidth: 1 } },
      },
    ],
  };
}

/** ISO weekday (1 = Mon) for a heatmap row index with rows Mon..Sun. */
export function weekdayForRow(row: number): number {
  return row + 1;
}
