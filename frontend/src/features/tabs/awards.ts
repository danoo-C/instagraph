import { formatDuration, formatNumber, formatPercent } from "@/lib/format";

export type AwardUnit = "count" | "ratio" | "ms" | "per_message";

/** Format an award value by its unit (ratio → %, ms → duration, per_message → 2 decimals). */
export function formatAwardValue(v: number | null | undefined, unit: AwardUnit): string {
  if (v == null) return "—";
  switch (unit) {
    case "ratio":
      return formatPercent(v);
    case "ms":
      return formatDuration(v / 1000);
    case "per_message":
      return formatNumber(v, { maximumFractionDigits: 2 });
    default:
      return formatNumber(v);
  }
}
