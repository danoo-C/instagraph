import { useMemo } from "react";
import { ChartCard } from "@/charts/ChartCard";
import { EChart } from "@/charts/EChart";
import { defaultGrid } from "@/charts/sampling";
import { PersonBadge } from "@/components/PersonBadge";
import { personColor } from "@/lib/colors";
import { formatNumber } from "@/lib/format";
import { bucketEnd } from "@/features/tabs/charts";
import type { SearchStats } from "@/features/messages/api";

/** Timeline of matches + per-person counts for the whole result set (§5.9). */
export function StatsPanel({
  stats,
  total,
  loading,
  onRange,
  onSender,
}: {
  stats: SearchStats | null | undefined;
  total: number | undefined;
  loading?: boolean;
  /** Click on a bucket: narrow the date range. */
  onRange: (start: string, end: string) => void;
  /** Click on a person: filter by sender. */
  onSender: (personId: number) => void;
}) {
  const tl = stats?.timeline;
  const option = useMemo(() => {
    const values = tl?.series[0]?.values ?? [];
    return {
      grid: defaultGrid(),
      tooltip: { trigger: "axis" as const },
      xAxis: { type: "category" as const, data: tl?.x ?? [] },
      yAxis: { type: "value" as const, minInterval: 1 },
      series: [
        {
          name: "Matches",
          type: "bar" as const,
          data: values,
          itemStyle: { color: "#0072B2" },
          barMaxWidth: 24,
        },
      ],
    };
  }, [tl]);
  const values = tl?.series[0]?.values ?? [];
  const perPerson = stats?.per_person ?? [];
  const max = Math.max(1, ...perPerson.map((p) => p.count));

  return (
    <div className="grid gap-3 lg:grid-cols-3">
      <ChartCard
        className="lg:col-span-2"
        title={`Matches over time${total != null ? ` · ${formatNumber(total)}` : ""}`}
        info={`Number of matching messages per ${tl?.bucket ?? "bucket"}. Click a bar to narrow the date range to it.`}
        loading={loading}
        empty={!loading && values.every((v) => !v)}
        height={160}
        data={
          tl
            ? {
                headers: ["date", "matches"],
                rows: tl.x.map((x, i) => [x, values[i] ?? 0]),
              }
            : null
        }
      >
        <EChart
          option={option}
          height={160}
          onEvents={{
            click: (p: { dataIndex: number }) => {
              const x = tl?.x[p.dataIndex];
              if (x && tl) onRange(x, bucketEnd(x, tl.bucket));
            },
          }}
        />
      </ChartCard>
      <ChartCard
        title="Matches per person"
        info="Matching messages per sender. Click a person to show only their messages."
        loading={loading}
        empty={!loading && perPerson.length === 0}
        height={160}
        data={{
          headers: ["person", "matches"],
          rows: perPerson.map((p) => [p.person.name, p.count]),
        }}
      >
        <ul className="flex max-h-48 flex-col gap-1 overflow-auto">
          {perPerson.map((p) => (
            <li key={p.person.person_id}>
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left text-sm hover:bg-surface-2"
                onClick={() => onSender(p.person.person_id)}
              >
                <PersonBadge
                  name={p.person.name}
                  id={p.person.person_id}
                  color_index={p.person.color_index}
                  size="xs"
                />
                <span className="min-w-0 flex-1 truncate">{p.person.name}</span>
                <span
                  className="h-2 rounded-full"
                  style={{
                    width: `${Math.max(4, (p.count / max) * 80)}px`,
                    backgroundColor: personColor({
                      id: p.person.person_id,
                      color_index: p.person.color_index,
                    }),
                  }}
                  aria-hidden="true"
                />
                <span className="w-14 text-right tabular-nums">{formatNumber(p.count)}</span>
              </button>
            </li>
          ))}
        </ul>
      </ChartCard>
    </div>
  );
}
