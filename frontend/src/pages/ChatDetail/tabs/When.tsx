import { escapeHtml } from "@/lib/utils";
import { categoryTable, heatmapTable } from "@/charts/tables";
import { useMemo, useState } from "react";
import { useApi } from "@/api/useApi";
import { ChartCard } from "@/charts/ChartCard";
import { recordsToTable } from "@/charts/csv";
import { EChart } from "@/charts/EChart";
import type { EChartsOption } from "@/charts/registerEcharts";
import { defaultGrid } from "@/charts/sampling";
import { CHART_TOKENS } from "@/charts/theme";
import { ErrorCard } from "@/components/ErrorCard";
import { PersonBadge } from "@/components/PersonBadge";
import { Tile, TileGrid } from "@/components/Tile";
import { diffMatrix, heatmapPoints, maxAbs, seriesColor } from "@/features/chat/charts";
import { parseChatId } from "@/features/chat/context";
import { useDrill } from "@/lib/drilldown";
import type { ApiCategoryChart, ApiHeatmap, ChatWhen } from "@/features/chat/types";
import { Segmented } from "@/components/Segmented";
import { SelectInput } from "@/components/SelectInput";
import { DIVERGING } from "@/lib/colors";
import { formatDate, formatHour, formatNumber, formatPercent } from "@/lib/format";
import { useResolvedTheme } from "@/state/theme";
import type { ChatTabProps } from "@/pages/ChatDetail/tabs";

type Scale = "count" | "share";
const SCALES: ReadonlyArray<{ value: Scale; label: string }> = [
  { value: "count", label: "Count" },
  { value: "share", label: "% of person" },
];

/** Convert a per-person category chart to "share of that person's messages". */
function asShare(chart: ApiCategoryChart): ApiCategoryChart {
  return {
    ...chart,
    unit: "ratio",
    series: chart.series.map((s) => {
      const total = s.values.reduce<number>((a, v) => a + (v ?? 0), 0);
      return { ...s, values: s.values.map((v) => (total ? (v ?? 0) / total : 0)) };
    }),
  };
}

function groupedBar(
  chart: ApiCategoryChart,
  opts: { stack?: boolean; labels?: (c: string) => string } = {},
): EChartsOption {
  const ratio = chart.unit === "ratio";
  return {
    legend: { type: "scroll", top: 0 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      valueFormatter: (v) => (ratio ? formatPercent(v as number) : formatNumber(v as number)),
    },
    grid: defaultGrid({ legend: true }),
    xAxis: {
      type: "category",
      data: chart.categories.map((c) => opts.labels?.(c) ?? c),
    },
    yAxis: {
      type: "value",
      axisLabel: ratio ? { formatter: (v: number) => formatPercent(v, 0) } : undefined,
    },
    series: chart.series.map((s) => ({
      type: "bar",
      name: s.name,
      color: seriesColor(s),
      stack: opts.stack ? "all" : undefined,
      data: s.values,
    })),
  };
}

export default function WhenTab({ chatId }: ChatTabProps) {
  const id = parseChatId(chatId);
  const q = useApi("/api/chats/{chat_id}/when", { path: { chat_id: id } });
  const open = useDrill();
  const theme = useResolvedTheme();
  const tk = CHART_TOKENS[theme];
  const d: ChatWhen | undefined = q.data;
  const [who, setWho] = useState<string>("all");
  const [hourScale, setHourScale] = useState<Scale>("count");
  const [dayScale, setDayScale] = useState<Scale>("count");

  const ownerHeat = d?.heatmaps.find((h) => h.person.is_owner);
  const others = useMemo(() => d?.heatmaps.filter((h) => !h.person.is_owner) ?? [], [d]);
  const canDiff = Boolean(ownerHeat && others.length > 0);

  const heat = useMemo(() => {
    if (!d) return null;
    if (who === "diff" && ownerHeat) {
      const rest = others.reduce<number[][]>(
        (acc, h) => acc.map((row, y) => row.map((v, x) => v + (h.heatmap.values[y]?.[x] ?? 0))),
        d.heatmap.values.map((row) => row.map(() => 0)),
      );
      const values = diffMatrix(ownerHeat.heatmap.values, rest);
      return {
        diverging: true,
        heatmap: { ...d.heatmap, values, unit: "ratio" } as ApiHeatmap,
        senderId: null as number | null,
      };
    }
    const person = d.heatmaps.find((h) => String(h.person.person_id) === who);
    return {
      diverging: false,
      heatmap: person?.heatmap ?? d.heatmap,
      senderId: person ? person.person.person_id : null,
    };
  }, [d, who, ownerHeat, others]);

  const heatOption = useMemo<EChartsOption | null>(() => {
    if (!heat) return null;
    const h = heat.heatmap;
    const lim = heat.diverging ? maxAbs(h.values) || 0.01 : 0;
    const max = heat.diverging ? lim : Math.max(1, ...h.values.flat());
    return {
      tooltip: {
        formatter: (p: unknown) => {
          const [x, y, v] = (p as { value: [number, number, number] }).value;
          const val = heat.diverging
            ? `${v >= 0 ? "+" : ""}${formatNumber(v * 100, { maximumFractionDigits: 2 })} pp`
            : `${formatNumber(v)} messages`;
          return `${escapeHtml(h.y_labels[y])} ${formatHour(x)}: ${val}`;
        },
      },
      grid: { left: 8, right: 16, top: 8, bottom: 56, containLabel: true },
      xAxis: { type: "category", data: h.x_labels, splitArea: { show: false } },
      yAxis: { type: "category", data: h.y_labels, inverse: true },
      visualMap: {
        min: heat.diverging ? -lim : 0,
        max,
        orient: "horizontal",
        left: "center",
        bottom: 0,
        calculable: false,
        itemHeight: 160,
        inRange: { color: heat.diverging ? [...DIVERGING] : [...tk.sequential] },
        formatter: heat.diverging
          ? (v: unknown) => `${formatNumber((v as number) * 100, { maximumFractionDigits: 1 })} pp`
          : undefined,
      },
      series: [
        {
          type: "heatmap",
          data: heatmapPoints(h.values),
          emphasis: { itemStyle: { borderWidth: 1 } },
        },
      ],
    };
  }, [heat, tk]);

  const smallMultiples = useMemo(
    () => (d ? hoursByYearOption(d.hours_by_year, tk) : null),
    [d, tk],
  );

  if (q.error && !d) return <ErrorCard error={q.error} onRetry={() => void q.refetch()} />;

  const base = { thread_id: id };
  const hours = d ? (hourScale === "share" ? asShare(d.hours) : d.hours) : null;
  const weekdays = d ? (dayScale === "share" ? asShare(d.weekdays) : d.weekdays) : null;
  const catClick = (chart: ApiCategoryChart | null | undefined) => ({
    click: (p: { dataIndex: number; seriesIndex: number }) => {
      const dq = chart?.drilldowns?.[p.dataIndex];
      const s = chart?.series[p.seriesIndex];
      if (dq) open({ ...dq, sender_id: s?.person_id ?? dq.sender_id ?? null });
    },
  });

  return (
    <div className="flex flex-col gap-4">
      <TileGrid>
        {d
          ? d.person_stats
              .flatMap((s) => [
                <Tile
                  key={`n${s.person.person_id}`}
                  label={
                    <>
                      <PersonBadge
                        name={s.person.name}
                        id={s.person.person_id}
                        color_index={s.person.color_index}
                        size="xs"
                      />{" "}
                      Night owl
                    </>
                  }
                  value={s.night_owl_ratio}
                  format="percent"
                  hint={`${s.person.name} · 00:00–05:00`}
                />,
                <Tile
                  key={`e${s.person.person_id}`}
                  label={
                    <>
                      <PersonBadge
                        name={s.person.name}
                        id={s.person.person_id}
                        color_index={s.person.color_index}
                        size="xs"
                      />{" "}
                      Early bird
                    </>
                  }
                  value={s.early_bird_ratio}
                  format="percent"
                  hint={`${s.person.name} · 05:00–08:00`}
                />,
              ])
              .slice(0, 12)
          : Array.from({ length: 4 }, (_, i) => <Tile key={i} label="…" value={null} loading />)}
        {d?.busiest_hour && (
          <Tile
            label="Busiest hour ever"
            value={d.busiest_hour.count}
            hint={`${formatDate(d.busiest_hour.date)}, ${formatHour(d.busiest_hour.hour)}`}
            onClick={() => open(d.busiest_hour!.drilldown)}
          />
        )}
      </TileGrid>

      <ChartCard
        title="Hour × weekday"
        info="When messages are sent, by weekday and hour (display time zone). Pick a person, or 'Difference' to compare my pattern with everyone else's (each normalized to its own total, in percentage points: blue = more me, orange = more them). Click a cell to read those messages."
        query={q}
        empty={q.isSuccess && !d?.heatmap.values.length}
        data={heat ? heatmapTable(heat.heatmap, "Weekday") : null}
        height={300}
        actions={
          d && (
            <SelectInput size="sm" value={who} onChange={setWho} label="Person">
              <option value="all">Everyone</option>
              {d.heatmaps.map((h) => (
                <option key={h.person.person_id} value={String(h.person.person_id)}>
                  {h.person.name}
                </option>
              ))}
              {canDiff && (
                <option value="diff">
                  Difference: me − {others.length === 1 ? others[0]!.person.name : "others"}
                </option>
              )}
            </SelectInput>
          )
        }
      >
        {heatOption && (
          <EChart
            option={heatOption}
            height={320}
            onEvents={{
              click: (p: { value: [number, number, number] }) => {
                const [x, y] = p.value;
                open({ ...base, hour: x, weekday: y + 1, sender_id: heat?.senderId ?? null });
              },
            }}
          />
        )}
      </ChartCard>

      <div className="card-grid">
        <ChartCard
          title="Hour of day"
          info="Messages per hour of the day, per person. '% of person' compares rhythms regardless of volume."
          query={q}
          data={hours ? categoryTable(hours, "Hour") : null}
          actions={
            <Segmented value={hourScale} options={SCALES} onChange={setHourScale} label="Scale" />
          }
        >
          {hours && (
            <EChart
              option={groupedBar(hours, { labels: (c) => c.padStart(2, "0") })}
              onEvents={catClick(d?.hours)}
            />
          )}
        </ChartCard>
        <ChartCard
          title="Day of week"
          info="Messages per weekday, per person."
          query={q}
          data={weekdays ? categoryTable(weekdays, "Weekday") : null}
          actions={
            <Segmented value={dayScale} options={SCALES} onChange={setDayScale} label="Scale" />
          }
        >
          {weekdays && <EChart option={groupedBar(weekdays)} onEvents={catClick(d?.weekdays)} />}
        </ChartCard>
        <ChartCard
          title="Month of year"
          info="Seasonality: messages per calendar month across all years, stacked by person."
          query={q}
          data={d ? categoryTable(d.months, "Month") : null}
        >
          {d && (
            <EChart option={groupedBar(d.months, { stack: true })} onEvents={catClick(d.months)} />
          )}
        </ChartCard>
        <ChartCard
          title="Weekday vs weekend"
          info="Share of each person's messages sent on weekdays (Mon–Fri) vs weekends (Sat–Sun)."
          query={q}
          data={d ? categoryTable(d.weekday_weekend, "Part of week") : null}
        >
          {d && <EChart option={weekendOption(d.weekday_weekend, tk)} />}
        </ChartCard>
      </div>

      <ChartCard
        title="Hourly pattern by year"
        info="Small multiples: share of each year's messages per hour of day — how the daily rhythm changed over the years."
        query={q}
        empty={q.isSuccess && !d?.hours_by_year.y_labels.length}
        data={d ? heatmapTable(d.hours_by_year, "Year") : null}
      >
        {smallMultiples && <EChart option={smallMultiples.option} height={smallMultiples.height} />}
      </ChartCard>

      <ChartCard
        title="Busiest days"
        info="The 10 days with the most messages. Click a day to read it."
        query={q}
        height={200}
        empty={q.isSuccess && !d?.busiest_days.length}
        data={
          d ? recordsToTable(d.busiest_days, ["date", { key: "count", header: "Messages" }]) : null
        }
      >
        <ol className="flex flex-col divide-y divide-border text-sm">
          {d?.busiest_days.map((b, i) => (
            <li key={b.date}>
              <button
                type="button"
                className="flex w-full items-center gap-3 px-1 py-1.5 text-left hover:bg-surface-2"
                onClick={() => open(b.drilldown)}
              >
                <span className="w-6 text-right tabular-nums text-muted">{i + 1}.</span>
                <span className="flex-1">{formatDate(b.date, { dateStyle: "full" })}</span>
                <span className="font-medium tabular-nums">{formatNumber(b.count)}</span>
              </button>
            </li>
          ))}
        </ol>
      </ChartCard>
    </div>
  );
}

function weekendOption(
  chart: ApiCategoryChart,
  tk: (typeof CHART_TOKENS)["light"] | (typeof CHART_TOKENS)["dark"],
): EChartsOption {
  // Categories = [Weekdays, Weekend]; one row per person.
  return {
    legend: { top: 0 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      valueFormatter: (v) => formatPercent(v as number),
    },
    grid: defaultGrid({ legend: true }),
    xAxis: { type: "value", max: 1, axisLabel: { formatter: (v: number) => formatPercent(v, 0) } },
    yAxis: { type: "category", data: chart.series.map((s) => s.name), inverse: true },
    series: chart.categories.map((c, ci) => ({
      type: "bar",
      name: c,
      stack: "all",
      color: ci === 0 ? DIVERGING[4] : DIVERGING[1],
      label: {
        show: true,
        color: ci === 0 ? "#fff" : tk.text,
        formatter: (p: unknown) => formatPercent((p as { value: number }).value, 0),
      },
      data: chart.series.map((s) => s.values[ci] ?? 0),
    })),
  };
}

function hoursByYearOption(
  h: ApiHeatmap,
  tk: (typeof CHART_TOKENS)["light"] | (typeof CHART_TOKENS)["dark"],
) {
  const n = h.y_labels.length;
  const cols = Math.min(4, Math.max(n, 1));
  const rows = Math.ceil(n / cols);
  const CELL_H = 130;
  const height = rows * CELL_H + 8;
  const max = Math.max(0.01, ...h.values.flat());
  const grids = h.y_labels.map((_, i) => {
    const r = Math.floor(i / cols);
    const c = i % cols;
    return {
      left: `${(c / cols) * 100 + 3}%`,
      width: `${100 / cols - 5}%`,
      top: r * CELL_H + 22,
      height: CELL_H - 50,
    };
  });
  const option: EChartsOption = {
    title: h.y_labels.map((y, i) => ({
      text: y,
      left: grids[i]!.left,
      top: Math.floor(i / cols) * CELL_H + 2,
      textStyle: { fontSize: 12, fontWeight: "normal", color: tk.muted },
    })),
    tooltip: {
      trigger: "axis",
      valueFormatter: (v) => formatPercent(v as number),
    },
    grid: grids,
    xAxis: grids.map((_, i) => ({
      type: "category",
      gridIndex: i,
      data: h.x_labels,
      axisLabel: { interval: 5, fontSize: 10 },
    })),
    yAxis: grids.map((_, i) => ({
      type: "value",
      gridIndex: i,
      max,
      axisLabel: { show: false },
      splitLine: { show: false },
    })),
    series: h.y_labels.map((y, i) => ({
      type: "bar",
      name: y,
      xAxisIndex: i,
      yAxisIndex: i,
      color: DIVERGING[4],
      data: h.values[i] ?? [],
    })),
  };
  return { option, height };
}
