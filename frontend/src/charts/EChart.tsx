import { useEffect, useMemo, useRef } from "react";
import ReactEChartsCore from "echarts-for-react/esm/core";
import { useChartSlot } from "@/charts/chartContext";
import { echarts, type EChartsOption, type EChartsType } from "@/charts/registerEcharts";
import { applySampling } from "@/charts/sampling";
import { chartThemeName } from "@/charts/theme";
import { useResolvedTheme } from "@/state/theme";

export interface EChartProps {
  option: EChartsOption;
  /** CSS height; default 320. Ignored in fullscreen. */
  height?: number | string;
  /** Accessible description; defaults to the enclosing ChartCard title. */
  ariaLabel?: string;
  /** ECharts events, e.g. `{ click: (p) => open(...) }` for drilldowns. */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- ECharts event params are untyped
  onEvents?: Record<string, (params: any, chart: EChartsType) => void>;
  /** Replace instead of merge on option change. Default true (safer for toggles). */
  notMerge?: boolean;
  /** Apply lttb/large sampling to long series. Default true. */
  sampling?: boolean;
  renderer?: "canvas" | "svg";
  className?: string;
  onReady?: (chart: EChartsType) => void;
}

/**
 * Thin ECharts wrapper: shared light/dark theme, a11y (aria), automatic lttb sampling,
 * auto-resize, and registration with the enclosing ChartCard for PNG export.
 */
export function EChart({
  option,
  height = 320,
  ariaLabel,
  onEvents,
  notMerge = true,
  sampling = true,
  renderer = "canvas",
  className,
  onReady,
}: EChartProps) {
  const theme = useResolvedTheme();
  const slot = useChartSlot();
  const label = ariaLabel ?? slot?.label;
  const register = slot?.register;

  const finalOption = useMemo<EChartsOption>(() => {
    const o = sampling ? applySampling(option) : option;
    return {
      animationDuration: 300,
      ...o,
      aria: {
        enabled: true,
        ...(label ? { label: { description: label } } : {}),
        ...(o.aria as object),
      },
    };
  }, [option, sampling, label]);

  const chartRef = useRef<EChartsType | null>(null);
  useEffect(
    () => () => {
      if (chartRef.current) register?.(null);
    },
    [register],
  );

  return (
    <ReactEChartsCore
      echarts={echarts}
      option={finalOption}
      theme={chartThemeName(theme)}
      notMerge={notMerge}
      lazyUpdate
      opts={{ renderer }}
      onEvents={onEvents}
      className={className}
      style={{ height: slot?.fullscreen ? "calc(100vh - 9rem)" : height, width: "100%" }}
      role="img"
      aria-label={label}
      onChartReady={(chart: EChartsType) => {
        chartRef.current = chart;
        register?.(chart);
        onReady?.(chart);
      }}
    />
  );
}
