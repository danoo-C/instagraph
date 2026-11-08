import type { ApiScalar, ApiUnit } from "@/features/people/apiTypes";
import {
  formatDate,
  formatDateTime,
  formatDuration,
  formatNumber,
  formatPercent,
} from "@/lib/format";

/** Format a backend scalar according to its `unit` (ms → duration, ratio → %, …). */
export function formatScalar(value: ApiScalar | undefined, unit?: ApiUnit | null): string {
  if (value == null || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string") {
    if (unit === "date") return formatDate(value);
    if (unit === "datetime") return formatDateTime(value);
    return value;
  }
  switch (unit) {
    case "ratio":
      return formatPercent(value);
    case "ms":
      return formatDuration(value / 1000);
    case "s":
      return formatDuration(value);
    case "per_message":
    case "per_day":
      return formatNumber(value, { maximumFractionDigits: 2 });
    default:
      return formatNumber(value);
  }
}

/** Index of the best value in a row (null when not comparable or all equal). */
export function bestIndex(values: ApiScalar[], lowerIsBetter = false): number | null {
  let best: number | null = null;
  let bestVal = 0;
  let distinct = false;
  values.forEach((v, i) => {
    if (typeof v !== "number" || Number.isNaN(v)) return;
    if (best === null) {
      best = i;
      bestVal = v;
      return;
    }
    if (v !== bestVal) distinct = true;
    if (lowerIsBetter ? v < bestVal : v > bestVal) {
      best = i;
      bestVal = v;
    }
  });
  return distinct ? best : null;
}
