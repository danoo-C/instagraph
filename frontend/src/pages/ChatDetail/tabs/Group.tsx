import { escapeHtml } from "@/lib/utils";
import { categoryTable, heatmapTable, timeSeriesTable } from "@/charts/tables";
import { useMemo, useState } from "react";
import { useApi } from "@/api/useApi";
import type { components } from "@/api/schema";
import { isApiError } from "@/api/errors";
import { ChartCard } from "@/charts/ChartCard";
import { recordsToTable } from "@/charts/csv";
import { EChart } from "@/charts/EChart";
import type { EChartsOption } from "@/charts/registerEcharts";
import { defaultGrid, timeSeriesZoom } from "@/charts/sampling";
import { CHART_TOKENS } from "@/charts/theme";
import { EmptyState } from "@/components/EmptyState";
import { ErrorCard } from "@/components/ErrorCard";
import { PersonBadge } from "@/components/PersonBadge";
import { ChartSkeleton } from "@/components/Skeleton";
import {
  bucketEnd,
  heatmapPoints,
  isEmptyChart,
  personRefColor,
  timeSeriesToEcharts,
} from "@/features/tabs/charts";
import { useDrill, useOpenContext } from "@/lib/drilldown";
import { chordGraph, membershipBars, type InteractionMatrix } from "@/features/tabs/group";
import { useChatHeader } from "@/features/chat/context";
import { Segmented } from "@/components/Segmented";
import { formatDate, formatDateTime, formatNumber, formatPercent } from "@/lib/format";
import { useFilters } from "@/state/filters";
import { useResolvedTheme } from "@/state/theme";
import type { ChatTabProps } from "@/pages/ChatDetail/tabs";

type GroupResponse = components["schemas"]["GroupResponse"];
type GroupEvent = components["schemas"]["GroupEvent"];
type Metric = "messages" | "words" | "reactions_received" | "media";

const METRICS: { value: Metric; label: string }[] = [
  { value: "messages", label: "Messages" },
  { value: "words", label: "Words" },
  { value: "reactions_received", label: "Reactions" },
  { value: "media", label: "Media" },
];

const EVENT_LABELS: Record<string, string> = {
  rename: "Renamed the group",
  join: "Added / joined",
  leave: "Left",
  removed: "Removed",
  photo_change: "Changed the group photo",
  theme_change: "Changed the theme",
  emoji_change: "Changed the quick reaction",
  nickname: "Set a nickname",
  admin: "Admin change",
};

export default function GroupTab({ chatId }: ChatTabProps) {
  const q = useApi("/api/chats/{chat_id}/group", { path: { chat_id: Number(chatId) } });
  if (q.isLoading) return <ChartSkeleton />;
  if (q.error && !q.data) {
    if (isApiError(q.error) && (q.error.code === "not_a_group" || q.error.status === 400))
      return (
        <EmptyState
          title="This is a 1:1 chat"
          description="Group stats (members, who replies to whom, membership) are only available for group chats."
        />
      );
    return <ErrorCard error={q.error} onRetry={() => void q.refetch()} />;
  }
  if (!q.data) return null;
  return <GroupContent data={q.data} chatId={chatId} />;
}

function GroupContent({ data, chatId }: { data: GroupResponse; chatId: string }) {
  const drill = useDrill();
  const tid = Number(chatId);
  const sizeBy = useMemo(
    () => Object.fromEntries(data.members.map((m) => [m.person.person_id, m.messages])),
    [data.members],
  );
  return (
    <div className="flex flex-col gap-4">
      <div className="card-grid">
        <Leaderboard data={data} />
        <ChartCard
          title="Lurkers"
          info="Members who wrote less than 1% of the group's messages."
          data={recordsToTable(
            data.lurkers.map((m) => ({
              name: m.person.name,
              messages: m.messages,
              share: m.share,
            })),
          )}
          empty={data.lurkers.length === 0}
          emptyMessage="No lurkers — everyone writes at least 1% of messages."
          height={200}
        >
          <ul className="flex max-h-80 flex-col gap-1 overflow-auto text-sm">
            {data.lurkers.map((m) => (
              <li key={m.person.person_id}>
                <button
                  type="button"
                  className="flex w-full items-center gap-2 rounded px-1 py-1 text-left hover:bg-surface-2"
                  onClick={() => drill(m.drilldown)}
                >
                  <PersonBadge
                    name={m.person.name}
                    id={m.person.person_id}
                    color_index={m.person.color_index}
                    showName
                    className="min-w-0 flex-1"
                  />
                  <span className="tabular-nums text-muted">
                    {formatNumber(m.messages)} · {formatPercent(m.share)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </ChartCard>
        <InteractionCard
          title="Who replies to whom"
          info="A's turn followed by B's within the reply window counts as B replying to A. Edge width = number of replies; arrows point from the replier to the person replied to."
          matrix={data.replies}
          sizeBy={sizeBy}
          chatId={tid}
          defaultView="chord"
        />
        <InteractionCard
          title="Who reacts to whom"
          info="Reactions given by each member (rows) to messages of each member (columns)."
          matrix={data.reactions}
          sizeBy={sizeBy}
          chatId={tid}
          defaultView="matrix"
        />
      </div>
      <ActivityChart data={data} />
      <Membership data={data} chatId={chatId} />
      <div className="card-grid">
        <EventList
          title="Membership events"
          info="Members added, joined, left or removed."
          events={data.membership_events}
          tid={tid}
        />
        <EventList
          title="Name & photo history"
          info="Group renames and photo, theme and quick-reaction changes."
          events={data.history}
          tid={tid}
        />
      </div>
      <Mentions data={data} />
    </div>
  );
}

function Leaderboard({ data }: { data: GroupResponse }) {
  const drill = useDrill();
  const [metric, setMetric] = useState<Metric>("messages");
  const rows = [...data.members].sort((a, b) => b[metric] - a[metric]);
  const label = METRICS.find((m) => m.value === metric)!.label;
  const option: EChartsOption = {
    grid: defaultGrid(),
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "value" },
    yAxis: { type: "category", inverse: true, data: rows.map((m) => m.person.name) },
    series: [
      {
        type: "bar",
        name: label,
        data: rows.map((m) => ({
          value: m[metric],
          itemStyle: { color: personRefColor(m.person) },
        })),
      },
    ],
  };
  return (
    <ChartCard
      title="Member leaderboard"
      info="Messages, words, reactions received and media (photos, videos, voice notes, GIFs, stickers) per member. Click a bar to see their messages."
      data={recordsToTable(
        rows.map((m) => ({
          name: m.person.name,
          messages: m.messages,
          share: m.share,
          words: m.words,
          reactions_received: m.reactions_received,
          media: m.media,
        })),
      )}
      empty={rows.length === 0}
      actions={<Segmented label="Metric" value={metric} options={METRICS} onChange={setMetric} />}
    >
      <EChart
        option={option}
        height={Math.max(240, rows.length * 26 + 40)}
        onEvents={{ click: (p) => drill(rows[p.dataIndex]?.drilldown) }}
      />
    </ChartCard>
  );
}

function InteractionCard({
  title,
  info,
  matrix,
  sizeBy,
  chatId,
  defaultView,
}: {
  title: string;
  info: string;
  matrix: InteractionMatrix;
  sizeBy: Record<number, number>;
  chatId: number;
  defaultView: "chord" | "matrix";
}) {
  const drill = useDrill();
  const theme = useResolvedTheme();
  const [view, setView] = useState<"chord" | "matrix">(defaultView);
  const empty = matrix.links.every((l) => l.value === 0) && heatmapPoints(matrix.matrix).max === 0;
  const people = matrix.people;

  const option = useMemo<EChartsOption>(() => {
    if (view === "chord") {
      const { nodes, links } = chordGraph(matrix, sizeBy);
      return {
        tooltip: {
          formatter: (p: { dataType?: string; data?: { value?: number }; name?: string }) => {
            if (p.dataType === "edge") {
              const d = p.data as { source: string; target: string; value: number };
              const n = (id: string) => people.find((x) => String(x.person_id) === id)?.name;
              return `${escapeHtml(n(d.source))} → ${escapeHtml(n(d.target))}: ${formatNumber(d.value)}`;
            }
            return `${escapeHtml(p.name)}: ${formatNumber(p.data?.value)} messages`;
          },
        },
        series: [
          {
            type: "graph",
            layout: "circular",
            circular: { rotateLabel: true },
            roam: true,
            label: { show: true, position: "right" },
            edgeSymbol: ["none", "arrow"],
            edgeSymbolSize: 6,
            emphasis: { focus: "adjacency", lineStyle: { width: 6 } },
            data: nodes,
            links,
          },
        ],
      } as unknown as EChartsOption;
    }
    const { data, max } = heatmapPoints(matrix.matrix);
    return {
      grid: { ...defaultGrid(), bottom: 60 },
      tooltip: {
        position: "top",
        formatter: (p: { value: [number, number, number] }) =>
          `${escapeHtml(matrix.matrix.y_labels[p.value[1]])} → ${escapeHtml(matrix.matrix.x_labels[p.value[0]])}: ${formatNumber(p.value[2])}`,
      },
      xAxis: {
        type: "category",
        data: matrix.matrix.x_labels,
        axisLabel: { rotate: 45 },
        splitArea: { show: true },
      },
      yAxis: { type: "category", data: matrix.matrix.y_labels, inverse: true },
      visualMap: {
        min: 0,
        max: Math.max(1, max),
        calculable: true,
        orient: "horizontal",
        left: "center",
        bottom: 0,
        itemHeight: 120,
        inRange: { color: [...CHART_TOKENS[theme].sequential] },
      },
      series: [{ type: "heatmap", data, label: { show: people.length <= 8 } }],
    } as unknown as EChartsOption;
  }, [view, matrix, sizeBy, people, theme]);

  return (
    <ChartCard
      title={title}
      info={info}
      data={heatmapTable(matrix.matrix, "from \\ to")}
      empty={empty}
      actions={
        <Segmented
          label="View"
          value={view}
          options={[
            { value: "chord", label: "Network" },
            { value: "matrix", label: "Matrix" },
          ]}
          onChange={setView}
        />
      }
    >
      <EChart
        option={option}
        height={Math.max(320, people.length * 28 + 80)}
        onEvents={{
          click: (p) => {
            if (view === "chord") {
              if (p.dataType === "edge")
                drill({ thread_id: chatId, sender_id: Number(p.data.source) });
              else if (p.dataType === "node")
                drill({ thread_id: chatId, sender_id: Number(p.data.id) });
            } else {
              const actor = people[p.value?.[1]];
              if (actor) drill({ thread_id: chatId, sender_id: actor.person_id });
            }
          },
        }}
      />
    </ChartCard>
  );
}

function ActivityChart({ data }: { data: GroupResponse }) {
  const drill = useDrill();
  const ts = data.activity;
  const option: EChartsOption = {
    grid: defaultGrid({ slider: true, legend: true }),
    legend: { type: "scroll", top: 0 },
    tooltip: { trigger: "axis" },
    dataZoom: timeSeriesZoom(),
    xAxis: { type: "time" },
    yAxis: { type: "value" },
    series: timeSeriesToEcharts(ts, { type: "line", stack: "total", area: true, smooth: true }),
  } as EChartsOption;
  return (
    <ChartCard
      title="Members' activity over time"
      info={`Messages per ${ts.bucket} per member, stacked. Click a point to open that period's messages.`}
      data={timeSeriesTable(ts)}
      empty={isEmptyChart(ts)}
    >
      <EChart
        option={option}
        height={360}
        onEvents={{
          click: (p) => {
            const x = ts.x[p.dataIndex];
            const s = ts.series[p.seriesIndex];
            if (x)
              drill({
                thread_id: data.chat_id,
                ...(s?.person_id != null ? { sender_id: s.person_id } : {}),
                start: x,
                end: bucketEnd(x, ts.bucket),
              });
          },
        }}
      />
    </ChartCard>
  );
}

function Membership({ data, chatId }: { data: GroupResponse; chatId: string }) {
  const header = useChatHeader(chatId);
  const { filters } = useFilters();
  const timeline = data.membership;
  const xs = data.activity.x;
  const spanStart =
    (header.data?.first_ts && Date.parse(header.data.first_ts)) || (xs[0] ? Date.parse(xs[0]) : 0);
  const spanEnd =
    (header.data?.last_ts && Date.parse(header.data.last_ts)) ||
    (xs.length ? Date.parse(xs[xs.length - 1]!) : spanStart);
  const bars = membershipBars(timeline, spanStart, spanEnd);
  const option = {
    grid: defaultGrid(),
    tooltip: {
      formatter: (p: { value: [number, number, number] }) => {
        const m = timeline[p.value[0]];
        return `${escapeHtml(m?.person.name)}<br/>${formatDate(p.value[1])} – ${formatDate(p.value[2])}`;
      },
    },
    xAxis: { type: "time", min: spanStart, max: spanEnd },
    yAxis: { type: "category", inverse: true, data: timeline.map((m) => m.person.name) },
    series: [
      {
        type: "custom",
        encode: { x: [1, 2], y: 0 },
        renderItem: (
          _params: unknown,
          api: {
            value: (i: number) => number;
            coord: (v: [number, number]) => [number, number];
            size: (v: [number, number]) => [number, number];
            style: () => Record<string, unknown>;
          },
        ) => {
          const y = api.value(0);
          const start = api.coord([api.value(1), y]);
          const end = api.coord([api.value(2), y]);
          const h = api.size([0, 1])[1] * 0.6;
          return {
            type: "rect",
            shape: {
              x: start[0],
              y: start[1] - h / 2,
              width: Math.max(2, end[0] - start[0]),
              height: h,
            },
            style: api.style(),
          };
        },
        data: bars.map((b) => ({
          value: b,
          itemStyle: { color: personRefColor(timeline[b[0]]!.person) },
        })),
      },
    ],
  } as unknown as EChartsOption;
  return (
    <ChartCard
      title="Membership timeline"
      info="When each member was in the group, from join/leave/removal events. Bars without a known start or end run to the edge of the chat's span."
      data={{
        headers: ["member", "from", "to"],
        rows: bars.map((b) => [
          timeline[b[0]]?.person.name,
          formatDateTime(b[1], filters.tz),
          formatDateTime(b[2], filters.tz),
        ]),
      }}
      empty={timeline.length === 0}
    >
      <EChart option={option} height={Math.max(200, timeline.length * 28 + 60)} />
    </ChartCard>
  );
}

function EventList({
  title,
  info,
  events,
  tid,
}: {
  title: string;
  info: string;
  events: GroupEvent[];
  tid: number;
}) {
  const openContext = useOpenContext();
  const { filters } = useFilters();
  return (
    <ChartCard
      title={title}
      info={info}
      data={recordsToTable(
        events.map((e) => ({
          ts: e.ts,
          event: e.event,
          actor: e.actor.name,
          member: e.member?.name ?? "",
          value: e.value ?? "",
        })),
      )}
      empty={events.length === 0}
      emptyMessage="No events in this period."
      height={200}
    >
      <ol className="relative flex max-h-96 flex-col gap-2 overflow-auto border-l border-border pl-4 text-sm">
        {events.map((e) => (
          <li key={e.message_id} className="relative">
            <span
              className="absolute -left-[1.3rem] top-1.5 h-2 w-2 rounded-full"
              style={{ backgroundColor: personRefColor(e.actor) }}
              aria-hidden="true"
            />
            <button
              type="button"
              className="w-full rounded px-1 text-left hover:bg-surface-2"
              onClick={() => openContext(e.message_id, tid)}
              title="Show in context"
            >
              <span className="block text-xs text-muted">{formatDateTime(e.ts, filters.tz)}</span>
              <span className="font-medium">{e.actor.name}</span>{" "}
              <span>{EVENT_LABELS[e.event] ?? e.event}</span>
              {e.member && e.member.person_id !== e.actor.person_id && (
                <span>: {e.member.name}</span>
              )}
              {e.value && !e.member && <span className="text-muted"> — “{e.value}”</span>}
            </button>
          </li>
        ))}
      </ol>
    </ChartCard>
  );
}

function Mentions({ data }: { data: GroupResponse }) {
  const drill = useDrill();
  const chart = data.mentions;
  const values = chart.series[0]?.values ?? [];
  const option: EChartsOption = {
    grid: defaultGrid(),
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "value" },
    yAxis: { type: "category", inverse: true, data: chart.categories },
    series: [
      {
        type: "bar",
        name: chart.series[0]?.name ?? "Mentions",
        data: values.map((v, i) => {
          const m = data.members.find((x) => x.person.name === chart.categories[i]);
          return { value: v, itemStyle: m ? { color: personRefColor(m.person) } : undefined };
        }),
      },
    ],
  };
  return (
    <ChartCard
      title="Most mentioned members"
      info="How often each member was @mentioned in the group."
      data={categoryTable(chart, "member")}
      empty={isEmptyChart(chart)}
    >
      <EChart
        option={option}
        height={Math.max(200, chart.categories.length * 26 + 40)}
        onEvents={{
          click: (p) => {
            const d = chart.drilldowns?.[p.dataIndex];
            if (d) drill(d);
          },
        }}
      />
    </ChartCard>
  );
}
