import type { EChartsOption } from "echarts";

/** Series longer than this get ECharts `lttb` sampling (§9). */
export const LTTB_THRESHOLD = 5000;

type AnySeries = Record<string, unknown> & { type?: string; data?: unknown };

function seriesLength(s: AnySeries): number {
  return Array.isArray(s.data) ? s.data.length : 0;
}

/**
 * Returns a copy of `option` where long line/area series use `sampling: "lttb"`
 * (and hide symbols), and long bar series use `large` mode. EChart applies this automatically.
 */
export function applySampling(option: EChartsOption, threshold = LTTB_THRESHOLD): EChartsOption {
  const series = option.series;
  if (!series) return option;
  const list = (Array.isArray(series) ? series : [series]) as AnySeries[];
  let changed = false;
  const next = list.map((s) => {
    const n = seriesLength(s);
    if (n <= threshold) return s;
    if (s.type === "line" && s.sampling === undefined) {
      changed = true;
      return { ...s, sampling: "lttb", showSymbol: false };
    }
    if (s.type === "bar" && s.large === undefined) {
      changed = true;
      return { ...s, large: true };
    }
    return s;
  });
  return changed ? ({ ...option, series: next } as EChartsOption) : option;
}

/**
 * dataZoom config for time series: mouse-wheel/pinch zoom + a slider under the chart.
 * Leave ~56px bottom grid space when `slider` is true.
 */
export function timeSeriesZoom(opts: { slider?: boolean; xAxisIndex?: number | number[] } = {}) {
  const { slider = true, xAxisIndex = 0 } = opts;
  const zoom: Array<Record<string, unknown>> = [
    {
      type: "inside",
      xAxisIndex,
      filterMode: "filter",
      zoomOnMouseWheel: "shift",
      moveOnMouseWheel: false,
    },
  ];
  if (slider)
    zoom.push({ type: "slider", xAxisIndex, height: 20, bottom: 8, filterMode: "filter" });
  return zoom;
}

/** Default grid with room for axis labels (and the zoom slider when used). */
export function defaultGrid(opts: { slider?: boolean; legend?: boolean } = {}) {
  return {
    left: 8,
    right: 16,
    top: opts.legend ? 36 : 16,
    bottom: opts.slider ? 48 : 8,
    containLabel: true,
  };
}
