import { categoryTable, heatmapTable, timeSeriesTable } from "@/charts/tables";
import { useMemo } from "react";
import { useApi } from "@/api/useApi";
import { ChartCard } from "@/charts/ChartCard";
import { recordsToTable } from "@/charts/csv";
import { EChart } from "@/charts/EChart";
import { defaultGrid } from "@/charts/sampling";
import { DataTable } from "@/components/DataTable";
import { dataTableColumnHelper } from "@/components/dataTableColumns";
import { EmptyState } from "@/components/EmptyState";
import { PersonBadge } from "@/components/PersonBadge";
import { TileGrid } from "@/components/Tile";
import type { components } from "@/api/schema";
import { bucketEnd, personRefColor, seriesColor } from "@/features/tabs/charts";
import { heatmapOption, personByName, weekdayForRow } from "@/features/tabs/callsHelpers";
import { ApiTileView } from "@/features/tabs/callsTile";
import { useDrill, useOpenContext } from "@/lib/drilldown";
import { TabState } from "@/features/tabs/ui";
import { useChatHeader } from "@/features/chat/context";
import { formatDateTime, formatDuration } from "@/lib/format";
import type { ChatTabProps } from "@/pages/ChatDetail/tabs";
import { useFilters } from "@/state/filters";
import { useResolvedTheme } from "@/state/theme";

type CallsResponse = components["schemas"]["CallsResponse"];
type CallItem = components["schemas"]["CallItem"];

const col = dataTableColumnHelper<CallItem>();

export default function CallsTab({ chatId }: ChatTabProps) {
  const q = useApi("/api/chats/{chat_id}/calls", { path: { chat_id: Number(chatId) } });
  return (
    <TabState query={q}>{() => <CallsBody data={q.data!} chatId={Number(chatId)} />}</TabState>
  );
}

function CallsBody({ data, chatId }: { data: CallsResponse; chatId: number }) {
  const drill = useDrill();
  const openContext = useOpenContext();
  const theme = useResolvedTheme();
  const { filters } = useFilters();
  const header = useChatHeader(chatId);
  const people = header.data?.participants;
  const callQ = data.drilldown;

  const columns = useMemo(
    () =>
      col.columns([
        col.accessor("ts", {
          header: "When",
          cell: (c) => formatDateTime(c.getValue(), filters.tz),
        }),
        col.accessor((r) => r.caller.name, {
          id: "caller",
          header: "Caller",
          cell: (c) => (
            <PersonBadge
              name={c.row.original.caller.name}
              id={c.row.original.caller.person_id}
              color_index={c.row.original.caller.color_index}
              showName
            />
          ),
        }),
        col.accessor((r) => r.kind ?? "unknown", { id: "kind", header: "Type" }),
        col.accessor((r) => r.duration_ms ?? 0, {
          id: "duration",
          header: "Duration",
          cell: (c) => formatDuration(c.getValue() / 1000),
        }),
      ]),
    [filters.tz],
  );

  if (data.total_calls === 0)
    return (
      <EmptyState
        title="No calls"
        description="Nobody called in this chat in the selected period."
      />
    );

  const pm = data.per_month;
  const calls = pm.series.find((s) => s.key === "calls");
  const minutes = pm.series.find((s) => s.key === "minutes");

  const starters = data.starters;
  const startersSeries = starters.series[0];
  const missed = data.missed_per_person;
  const missedSeries = missed.series[0];
  const hist = data.duration_histogram;
  const histSeries = hist.series[0];

  const senderQ = (name: string, extra?: Record<string, unknown>) => {
    const p = personByName(people, name);
    drill(callQ, { ...(p ? { sender_id: p.person_id } : {}), ...(extra as object) });
  };

  return (
    <div className="flex flex-col gap-4">
      <TileGrid>
        {data.tiles.map((t) => (
          <ApiTileView key={t.key} tile={t} timeZone={filters.tz} />
        ))}
      </TileGrid>

      <div className="card-grid">
        <ChartCard
          title="Calls and minutes per month"
          info="Bars: number of calls per month. Line: minutes spent on connected calls."
          data={timeSeriesTable(pm, "month")}
          empty={!calls}
        >
          <EChart
            option={{
              grid: defaultGrid({ legend: true }),
              legend: { top: 0 },
              tooltip: { trigger: "axis" },
              xAxis: { type: "time" },
              yAxis: [
                { type: "value", name: "Calls", minInterval: 1 },
                { type: "value", name: "Minutes", splitLine: { show: false } },
              ],
              series: [
                {
                  name: calls?.name ?? "Calls",
                  type: "bar",
                  itemStyle: { color: seriesColor({ key: "calls" }, 0) },
                  data: pm.x.map((x, i) => [x, calls?.values[i] ?? 0]),
                },
                {
                  name: minutes?.name ?? "Minutes",
                  type: "line",
                  yAxisIndex: 1,
                  showSymbol: pm.x.length < 60,
                  itemStyle: { color: seriesColor({ key: "minutes" }, 1) },
                  data: pm.x.map((x, i) => [x, minutes?.values[i] ?? 0]),
                },
              ],
            }}
            onEvents={{
              click: (p: { value: [string, number] }) => {
                const start = p.value[0];
                drill(callQ, { start, end: bucketEnd(start, pm.bucket) });
              },
            }}
          />
        </ChartCard>

        <ChartCard
          title="Who starts calls"
          info="Calls started per person (the caller is the sender of the call record)."
          data={categoryTable(starters, "person")}
          empty={!startersSeries || starters.categories.length === 0}
        >
          <EChart
            option={{
              tooltip: { trigger: "item" },
              legend: { bottom: 0 },
              series: [
                {
                  type: "pie",
                  radius: ["45%", "70%"],
                  label: { formatter: "{b}: {d}%" },
                  data: starters.categories.map((name, i) => {
                    const p = personByName(people, name);
                    return {
                      name,
                      value: startersSeries?.values[i] ?? 0,
                      itemStyle: { color: p ? personRefColor(p) : seriesColor({ key: name }, i) },
                    };
                  }),
                },
              ],
            }}
            onEvents={{ click: (p: { name: string }) => senderQ(p.name) }}
          />
        </ChartCard>

        <ChartCard
          title="Missed calls per person"
          info="Calls that were not answered, counted for the person who called."
          data={categoryTable(missed, "person")}
          empty={!missedSeries || missed.categories.length === 0}
          emptyMessage="No missed calls"
        >
          <EChart
            option={{
              grid: defaultGrid(),
              tooltip: { trigger: "axis" },
              xAxis: { type: "category", data: missed.categories },
              yAxis: { type: "value", minInterval: 1 },
              series: [
                {
                  type: "bar",
                  name: "Missed",
                  data: missed.categories.map((name, i) => {
                    const p = personByName(people, name);
                    return {
                      value: missedSeries?.values[i] ?? 0,
                      itemStyle: { color: p ? personRefColor(p) : seriesColor({ key: name }, i) },
                    };
                  }),
                },
              ],
            }}
            onEvents={{ click: (p: { name: string }) => senderQ(p.name, { call_missed: true }) }}
          />
        </ChartCard>

        <ChartCard
          title="Call duration distribution"
          info="Connected calls (not missed, duration > 0) grouped by length."
          data={categoryTable(hist, "duration")}
          empty={!histSeries}
        >
          <EChart
            option={{
              grid: defaultGrid(),
              tooltip: { trigger: "axis" },
              xAxis: { type: "category", data: hist.categories },
              yAxis: { type: "value", minInterval: 1 },
              series: [
                {
                  type: "bar",
                  name: "Calls",
                  barCategoryGap: "5%",
                  itemStyle: { color: seriesColor({ key: "calls" }, 0) },
                  data: histSeries?.values ?? [],
                },
              ],
            }}
          />
        </ChartCard>

        <ChartCard
          title="Calls by hour and weekday"
          info="When calls happen: weekday (rows) by hour of day (columns), in the display time zone."
          data={heatmapTable(data.heatmap, "weekday")}
        >
          <EChart
            option={heatmapOption(data.heatmap, theme, { xName: "Hour", valueLabel: "Calls" })}
            onEvents={{
              click: (p: { value: [number, number, number] }) =>
                drill(callQ, {
                  hour: Number(data.heatmap.x_labels[p.value[0]] ?? p.value[0]),
                  weekday: weekdayForRow(p.value[1]),
                }),
            }}
          />
        </ChartCard>

        <ChartCard
          title="Longest calls"
          info="Top 10 connected calls by duration. Click a row to see the messages around it."
          data={recordsToTable(
            data.longest_calls.map((c) => ({
              when: c.ts,
              caller: c.caller.name,
              kind: c.kind ?? "",
              duration_s: (c.duration_ms ?? 0) / 1000,
            })),
          )}
          empty={data.longest_calls.length === 0}
        >
          <DataTable
            data={data.longest_calls}
            columns={columns}
            getRowId={(r) => String(r.message_id)}
            onRowClick={(r) => openContext(r.message_id, chatId)}
            caption="Longest calls"
          />
        </ChartCard>
      </div>
    </div>
  );
}
