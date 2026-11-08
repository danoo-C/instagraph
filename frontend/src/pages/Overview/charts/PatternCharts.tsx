import { categoryTable } from "@/charts/tables";
import { useMemo } from "react";
import { useApi } from "@/api/useApi";
import { ChartCard } from "@/charts/ChartCard";
import { EChart } from "@/charts/EChart";
import { CHART_TOKENS } from "@/charts/theme";
import { toMessageQuery, useOpenMessages } from "@/lib/drilldown";

import {
  calendarHeight,
  calendarOption,
  hoursOption,
  punchCardOption,
  weekdaysOption,
} from "@/features/overview/transforms";
import { personColor } from "@/lib/colors";
import { formatNumber } from "@/lib/format";
import { useResolvedTheme } from "@/state/theme";

type ClickParams = { dataIndex?: number; value?: unknown };

/** 5. Calendar heatmap, one row per year. */
export function CalendarCard() {
  const q = useApi("/api/overview/calendar");
  const theme = useResolvedTheme();
  const open = useOpenMessages();
  const d = q.data;
  const option = useMemo(
    () => (d ? calendarOption(d.calendar, d.years, CHART_TOKENS[theme].sequential) : {}),
    [d, theme],
  );
  return (
    <ChartCard
      title="Calendar"
      info={`Messages per day, one row per year (darker = more). ${d ? `${formatNumber(d.active_days)} active days.` : ""} Click a day to open its messages.`}
      query={q}
      empty={q.isSuccess && (d?.calendar.days.length ?? 0) === 0}
      data={
        d
          ? {
              headers: ["date", "messages"],
              rows: d.calendar.days.map((x) => [x.date, x.value]),
            }
          : null
      }
      className="xl:col-span-2"
      height={calendarHeight(2)}
    >
      <EChart
        option={option}
        height={calendarHeight(d?.years.length ?? 1)}
        onEvents={{
          click: (p: ClickParams) => {
            const v = p.value as [string, number] | undefined;
            if (v?.[0]) open({ start: v[0], end: v[0] });
          },
        }}
      />
    </ChartCard>
  );
}

/** 6. Hour × weekday punch card. */
export function PunchCardCard() {
  const q = useApi("/api/overview/heatmap");
  const open = useOpenMessages();
  const hm = q.data?.heatmap;
  const option = useMemo(() => (hm ? punchCardOption(hm, personColor(0)) : {}), [hm]);
  return (
    <ChartCard
      title="Hour × weekday"
      info="When messages are sent: bubble size = number of messages at that hour on that weekday (your display time zone). Click a bubble to open those messages."
      query={q}
      empty={q.isSuccess && (hm?.values.flat().every((v) => !v) ?? true)}
      data={
        hm
          ? {
              headers: ["weekday", ...hm.x_labels],
              rows: hm.y_labels.map((y, i) => [y, ...(hm.values[i] ?? [])]),
            }
          : null
      }
    >
      <EChart
        option={option}
        onEvents={{
          click: (p: ClickParams) => {
            const v = p.value as [number, number, number] | undefined;
            if (v) open({ hour: Number(hm?.x_labels[v[0]] ?? v[0]), weekday: v[1] + 1 });
          },
        }}
      />
    </ChartCard>
  );
}

/** 7. Hour of day, radial clock, me vs others. */
export function HoursCard() {
  const q = useApi("/api/overview/hours");
  const open = useOpenMessages();
  const chart = q.data?.chart;
  const option = useMemo(() => (chart ? hoursOption(chart) : {}), [chart]);
  return (
    <ChartCard
      title="Hour of day"
      info="24-hour clock of messages: yours vs everyone else's. Midnight is at the top. Click a bar to open that hour's messages."
      query={q}
      empty={q.isSuccess && !chart?.series.some((s) => s.values.some(Boolean))}
      data={categoryTable(chart, "hour")}
    >
      <EChart
        option={option}
        onEvents={{
          click: (p: ClickParams) => {
            if (p.dataIndex == null) return;
            const dq = chart?.drilldowns?.[p.dataIndex];
            open(dq ? toMessageQuery(dq) : { hour: p.dataIndex });
          },
        }}
      />
    </ChartCard>
  );
}

/** 8. Day of week. */
export function WeekdaysCard() {
  const q = useApi("/api/overview/weekdays");
  const open = useOpenMessages();
  const chart = q.data?.chart;
  const option = useMemo(() => (chart ? weekdaysOption(chart) : {}), [chart]);
  return (
    <ChartCard
      title="Day of week"
      info="Messages per weekday, yours vs everyone else's. Click a bar to open that weekday's messages."
      query={q}
      empty={q.isSuccess && !chart?.series.some((s) => s.values.some(Boolean))}
      data={categoryTable(chart, "weekday")}
    >
      <EChart
        option={option}
        onEvents={{
          click: (p: ClickParams) => {
            if (p.dataIndex == null) return;
            const dq = chart?.drilldowns?.[p.dataIndex];
            open(dq ? toMessageQuery(dq) : { weekday: p.dataIndex + 1 });
          },
        }}
      />
    </ChartCard>
  );
}
