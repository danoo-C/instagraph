import { escapeHtml } from "@/lib/utils";
import { timeSeriesTable } from "@/charts/tables";
import { useMemo, useState } from "react";
import { useApi } from "@/api/useApi";
import { ChartCard } from "@/charts/ChartCard";
import { recordsToTable } from "@/charts/csv";
import { EChart } from "@/charts/EChart";
import type { EChartsOption } from "@/charts/registerEcharts";
import { defaultGrid, timeSeriesZoom } from "@/charts/sampling";
import { CHART_TOKENS } from "@/charts/theme";
import { ErrorCard } from "@/components/ErrorCard";
import { Tile, TileGrid } from "@/components/Tile";
import { bucketRange, seriesColor, trendArrow } from "@/features/chat/charts";
import { parseChatId } from "@/features/chat/context";
import { useDrill } from "@/lib/drilldown";
import type { ApiTimeSeries, Bucket, ChatTimeline, TimelineEvent } from "@/features/chat/types";
import { Segmented } from "@/components/Segmented";
import { DIVERGING } from "@/lib/colors";
import { formatDate, formatMonth, formatNumber, formatPercent } from "@/lib/format";
import { useResolvedTheme } from "@/state/theme";
import type { ChatTabProps } from "@/pages/ChatDetail/tabs";

const BUCKETS: ReadonlyArray<{ value: Bucket; label: string }> = [
  { value: "day", label: "Day" },
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
  { value: "year", label: "Year" },
];

const EVENT_LABEL: Record<TimelineEvent["kind"], string> = {
  first_message: "First message",
  peak_day: "Peak day",
  peak_month: "Peak month",
  silence: "Silence",
  rename: "Rename",
  join: "Joined",
  leave: "Left",
  removed: "Removed",
  first_call: "First call",
  custom_term: "Term",
};

/** Line series on a time axis from a TimeSeries. */
function timeLines(ts: ApiTimeSeries, extra: Record<string, unknown> = {}) {
  return ts.series.map((s) => ({
    type: "line" as const,
    name: s.name,
    showSymbol: false,
    color: seriesColor(s),
    data: ts.x.map((x, i) => [x, s.values[i] ?? null]),
    ...extra,
  }));
}

export default function TimelineTab({ chatId }: ChatTabProps) {
  const [bucket, setBucket] = useState<Bucket>("week");
  const id = parseChatId(chatId);
  const q = useApi("/api/chats/{chat_id}/timeline", { path: { chat_id: id }, query: { bucket } });
  const open = useDrill();
  const theme = useResolvedTheme();
  const d: ChatTimeline | undefined = q.data;

  const options = useMemo(() => (d ? buildOptions(d, theme) : null), [d, theme]);

  if (q.error && !d) return <ErrorCard error={q.error} onRetry={() => void q.refetch()} />;

  const base = { thread_id: id };
  const lc = d?.lifecycle;
  const trend = d ? trendArrow(d.trend) : null;

  return (
    <div className="flex flex-col gap-4">
      <TileGrid>
        <Tile
          label="Trend (last 6 months)"
          loading={!d}
          value={trend ? `${trend.symbol} ${trend.label}` : null}
          format="text"
          hint={
            d ? `slope ${formatNumber(d.trend_slope, { maximumFractionDigits: 2 })}` : undefined
          }
        />
        <Tile
          label="Peak month"
          loading={!d}
          value={lc?.peak_month ? formatMonth(lc.peak_month) : null}
          format="text"
          hint={lc ? `${formatNumber(lc.peak_month_count)} messages` : undefined}
          onClick={
            lc?.peak_month
              ? () => open({ ...base, ...bucketRange(lc.peak_month!, "month") })
              : undefined
          }
        />
        <Tile label="Active months" loading={!d} value={lc?.active_months} />
        <Tile
          label="Longest break"
          loading={!d}
          value={lc ? `${formatNumber(lc.longest_break_days)} days` : null}
          format="text"
          hint={
            lc?.break_start
              ? `${formatDate(lc.break_start)} → ${formatDate(lc.comeback)}`
              : undefined
          }
        />
        {d?.balance_overall != null && (
          <Tile
            label="Balance index"
            value={d.balance_overall}
            format="percent"
            hint="1 = perfectly balanced"
          />
        )}
      </TileGrid>

      <ChartCard
        title="Messages over time"
        info="Messages per period, stacked by person. Drag the slider (or shift + scroll) to zoom; click a bar to read that period's messages."
        query={q}
        empty={q.isSuccess && !d?.messages.x.length}
        data={d ? timeSeriesTable(d.messages) : null}
        actions={
          <Segmented value={bucket} options={BUCKETS} onChange={setBucket} label="Time bucket" />
        }
      >
        {options && d && (
          <EChart
            option={options.messages}
            height={340}
            onEvents={{
              click: (p: { dataIndex: number; seriesIndex: number }) => {
                const x = d.messages.x[p.dataIndex];
                const s = d.messages.series[p.seriesIndex];
                if (x)
                  open({
                    ...base,
                    ...bucketRange(x, d.messages.bucket),
                    sender_id: s?.person_id ?? null,
                  });
              },
            }}
          />
        )}
      </ChartCard>

      <div className="card-grid">
        <ChartCard
          title="Share over time"
          info="Each person's share of the month's messages (100% stacked) — who carries the chat."
          query={q}
          empty={q.isSuccess && !d?.share.x.length}
          data={d ? timeSeriesTable(d.share) : null}
        >
          {options && <EChart option={options.share} height={300} />}
        </ChartCard>
        {d?.balance ? (
          <ChartCard
            title="Balance per month"
            info="Balance index = 1 − |my share − 0.5| × 2 (1 = perfectly balanced, 0 = one-sided). The dashed line is my monthly share; 0.5 means both sent the same."
            query={q}
            data={d ? timeSeriesTable(d.balance) : null}
          >
            {options?.balance && <EChart option={options.balance} height={300} />}
          </ChartCard>
        ) : (
          <ChartCard
            title="Cumulative messages"
            info="Running total of messages per person — shows who is 'ahead'."
            query={q}
            data={d ? timeSeriesTable(d.cumulative) : null}
          >
            {options && <EChart option={options.cumulative} height={300} />}
          </ChartCard>
        )}
        {d?.balance && (
          <ChartCard
            title="Cumulative messages"
            info="Running total of messages per person — shows who is 'ahead'."
            query={q}
            data={timeSeriesTable(d.cumulative)}
          >
            {options && <EChart option={options.cumulative} height={300} />}
          </ChartCard>
        )}
        <ChartCard
          title="Rolling average"
          info="Messages per day (bars) with 7-day and 30-day rolling averages."
          query={q}
          data={d ? timeSeriesTable(d.rolling) : null}
        >
          {options && (
            <EChart
              option={options.rolling}
              height={300}
              onEvents={{
                click: (p: { value: [string, number] }) => {
                  const day = Array.isArray(p.value) ? p.value[0] : undefined;
                  if (day) open({ ...base, start: day, end: day });
                },
              }}
            />
          )}
        </ChartCard>
        <ChartCard
          title="Words per message"
          info="Average words per text message per month, per person."
          query={q}
          data={d ? timeSeriesTable(d.words_per_message) : null}
        >
          {options && <EChart option={options.wpm} height={300} />}
        </ChartCard>
      </div>

      <ChartCard
        title="Activity calendar"
        info="Messages per day. Darker = more messages. Click a day to read it."
        query={q}
        empty={q.isSuccess && !d?.calendar.days.length}
        data={
          d ? recordsToTable(d.calendar.days, ["date", { key: "value", header: "Messages" }]) : null
        }
        height={200}
      >
        {options && (
          <EChart
            option={options.calendar.option}
            height={options.calendar.height}
            onEvents={{
              click: (p: { value: [string, number] }) => {
                const day = p.value?.[0];
                if (day) open({ ...base, start: day, end: day });
              },
            }}
          />
        )}
      </ChartCard>

      <ChartCard
        title="Events"
        info="Milestones of this chat: first message, first call, peaks, long silences and group changes. Click a marker to open the messages behind it."
        query={q}
        empty={q.isSuccess && !d?.events.length}
        data={
          d
            ? recordsToTable(
                d.events.map((e) => ({
                  date: e.date,
                  kind: e.kind,
                  label: e.label,
                  person: e.person?.name,
                })),
              )
            : null
        }
      >
        {options && d && (
          <>
            <EChart
              option={options.events}
              height={220}
              onEvents={{
                click: (p: { data?: { idx?: number } }) => {
                  const e = p.data?.idx != null ? d.events[p.data.idx] : undefined;
                  if (e?.drilldown) open(e.drilldown);
                },
              }}
            />
            <ol className="mt-2 flex flex-col gap-1 text-sm">
              {d.events.map((e, i) => (
                <li key={i} className="flex gap-3">
                  <time className="w-28 shrink-0 tabular-nums text-muted" dateTime={e.date}>
                    {formatDate(e.date)}
                  </time>
                  {e.drilldown ? (
                    <button
                      type="button"
                      className="text-left hover:text-accent hover:underline"
                      onClick={() => open(e.drilldown)}
                    >
                      {e.label}
                    </button>
                  ) : (
                    <span>{e.label}</span>
                  )}
                  {e.person && <span className="text-muted">· {e.person.name}</span>}
                </li>
              ))}
            </ol>
          </>
        )}
      </ChartCard>
    </div>
  );
}

function buildOptions(d: ChatTimeline, theme: "light" | "dark") {
  const tk = CHART_TOKENS[theme];
  const legend = { type: "scroll" as const, top: 0 };

  const messages: EChartsOption = {
    legend,
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: defaultGrid({ slider: true, legend: true }),
    dataZoom: timeSeriesZoom(),
    xAxis: {
      type: "category",
      data: d.messages.x,
      axisLabel: {
        formatter: (v: string) =>
          d.messages.bucket === "year"
            ? v.slice(0, 4)
            : d.messages.bucket === "month"
              ? formatMonth(v)
              : formatDate(v),
      },
    },
    yAxis: { type: "value", name: "Messages" },
    series: d.messages.series.map((s) => ({
      type: "bar",
      name: s.name,
      stack: "msgs",
      color: seriesColor(s),
      data: s.values,
      large: d.messages.x.length > 2000,
    })),
  };

  const share: EChartsOption = {
    legend,
    tooltip: {
      trigger: "axis",
      valueFormatter: (v) => formatPercent(v as number),
    },
    grid: defaultGrid({ slider: true, legend: true }),
    dataZoom: timeSeriesZoom(),
    xAxis: { type: "time" },
    yAxis: { type: "value", max: 1, axisLabel: { formatter: (v: number) => formatPercent(v, 0) } },
    series: timeLines(d.share, {
      stack: "share",
      areaStyle: { opacity: 0.85 },
      lineStyle: { width: 0 },
    }),
  };

  let balance: EChartsOption | null = null;
  if (d.balance) {
    const me = d.share.series[0];
    balance = {
      legend,
      tooltip: { trigger: "axis", valueFormatter: (v) => formatPercent(v as number) },
      grid: defaultGrid({ slider: true, legend: true }),
      dataZoom: timeSeriesZoom(),
      xAxis: { type: "time" },
      yAxis: {
        type: "value",
        min: 0,
        max: 1,
        axisLabel: { formatter: (v: number) => formatPercent(v, 0) },
      },
      series: [
        {
          type: "line",
          name: "Balance index",
          showSymbol: false,
          color: DIVERGING[4],
          data: d.balance.x.map((x, i) => [x, d.balance!.series[0]?.values[i] ?? null]),
          markLine: {
            silent: true,
            symbol: "none",
            lineStyle: { color: tk.muted, type: "solid" },
            label: { formatter: "0.5", color: tk.muted },
            data: [{ yAxis: 0.5 }],
          },
        },
        ...(me
          ? [
              {
                type: "line" as const,
                name: `${me.name} share`,
                showSymbol: false,
                color: seriesColor(me),
                lineStyle: { type: "dashed" as const },
                data: d.share.x.map((x, i) => [x, me.values[i] ?? null]),
              },
            ]
          : []),
      ],
    };
  }

  const cumulative: EChartsOption = {
    legend,
    tooltip: { trigger: "axis" },
    grid: defaultGrid({ slider: true, legend: true }),
    dataZoom: timeSeriesZoom(),
    xAxis: { type: "time" },
    yAxis: { type: "value" },
    series: timeLines(d.cumulative),
  };

  const rolling: EChartsOption = {
    legend,
    tooltip: { trigger: "axis", valueFormatter: (v) => formatNumber(v as number) },
    grid: defaultGrid({ slider: true, legend: true }),
    dataZoom: timeSeriesZoom(),
    xAxis: { type: "time" },
    yAxis: { type: "value", name: "Messages/day" },
    series: d.rolling.series.map((s) =>
      s.key === "daily"
        ? {
            type: "bar" as const,
            name: s.name,
            color: tk.axis,
            barMaxWidth: 4,
            data: d.rolling.x.map((x, i) => [x, s.values[i] ?? null]),
          }
        : {
            type: "line" as const,
            name: s.name,
            showSymbol: false,
            color: s.key === "avg_7" ? DIVERGING[1] : DIVERGING[4],
            data: d.rolling.x.map((x, i) => [x, s.values[i] ?? null]),
          },
    ),
  };

  const wpm: EChartsOption = {
    legend,
    tooltip: {
      trigger: "axis",
      valueFormatter: (v) => formatNumber(v as number, { maximumFractionDigits: 2 }),
    },
    grid: defaultGrid({ slider: true, legend: true }),
    dataZoom: timeSeriesZoom(),
    xAxis: { type: "time" },
    yAxis: { type: "value", name: "Words/msg" },
    series: timeLines(d.words_per_message, { connectNulls: true }),
  };

  // Calendar: one row per year.
  const years = Array.from(new Set(d.calendar.days.map((x) => x.date.slice(0, 4)))).sort();
  const CAL_H = 130;
  const calendar = {
    height: Math.max(years.length, 1) * CAL_H + 60,
    option: {
      tooltip: {
        formatter: (p: { value: [string, number] }) =>
          `${formatDate(p.value[0])}: ${formatNumber(p.value[1])} messages`,
      },
      visualMap: {
        min: 0,
        max: Math.max(d.calendar.max_value, 1),
        type: "continuous",
        orient: "horizontal",
        left: "center",
        bottom: 0,
        itemHeight: 120,
        calculable: false,
        inRange: { color: [...tk.sequential] },
      },
      calendar: years.map((y, i) => ({
        range: y,
        top: 24 + i * CAL_H,
        left: 40,
        right: 16,
        cellSize: ["auto", 13],
        yearLabel: { show: true, position: "left" as const },
        dayLabel: { firstDay: 1, nameMap: "en" },
      })),
      series: years.map((y, i) => ({
        type: "heatmap" as const,
        coordinateSystem: "calendar" as const,
        calendarIndex: i,
        data: d.calendar.days.filter((x) => x.date.startsWith(y)).map((x) => [x.date, x.value]),
      })),
    } as unknown as EChartsOption,
  };

  // Events: markers on a time axis, one row per event kind.
  const kinds = Array.from(new Set(d.events.map((e) => e.kind)));
  const events: EChartsOption = {
    tooltip: {
      trigger: "item",
      formatter: (p: unknown) => {
        const data = (p as { data?: { idx?: number } }).data;
        const e = data?.idx != null ? d.events[data.idx] : undefined;
        return e ? `${formatDate(e.date)}<br/>${escapeHtml(e.label)}` : "";
      },
    },
    grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: "time" },
    yAxis: {
      type: "category",
      data: kinds.map((k) => EVENT_LABEL[k]),
      splitLine: { show: true, lineStyle: { color: tk.grid } },
    },
    series: [
      {
        type: "scatter",
        symbolSize: 12,
        symbol: "pin",
        data: d.events.map((e, idx) => ({
          value: [e.date, kinds.indexOf(e.kind)],
          idx,
          itemStyle: {
            color: e.person
              ? seriesColor({
                  color_index: e.person.color_index,
                  person_id: e.person.person_id,
                  key: "",
                })
              : DIVERGING[0],
          },
        })),
      },
    ],
  };

  return { messages, share, balance, cumulative, rolling, wpm, calendar, events };
}
