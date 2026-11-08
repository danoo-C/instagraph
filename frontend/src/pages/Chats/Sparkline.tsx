import { sparklinePoints } from "@/features/chat/charts";
import { formatMonth, formatNumber } from "@/lib/format";

const W = 96;
const H = 24;

/** Tiny SVG line of monthly message counts (cheap enough for hundreds of rows). */
export function Sparkline({ values, months }: { values: number[]; months: string[] }) {
  const peak = values.reduce((m, v, i) => (v > (values[m] ?? 0) ? i : m), 0);
  const label =
    values.length > 0
      ? `Monthly activity, peak ${formatNumber(values[peak])} in ${formatMonth(months[peak])}`
      : "No activity";
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
      <title>{label}</title>
      <polyline
        points={sparklinePoints(values, W, H)}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.25}
        className="text-accent"
        strokeLinejoin="round"
      />
    </svg>
  );
}
