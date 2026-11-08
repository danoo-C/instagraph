import type { ApiTimeSeries } from "@/features/overview/types";

/** True when a time series has no non-zero values. */
export function isEmptySeries(ts: ApiTimeSeries | undefined): boolean {
  if (!ts || ts.x.length === 0) return true;
  return ts.series.every((s) => s.values.every((v) => !v));
}
