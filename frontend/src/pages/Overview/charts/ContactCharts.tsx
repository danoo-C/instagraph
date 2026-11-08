import { timeSeriesTable } from "@/charts/tables";
import { useMemo, useState } from "react";
import { useApi } from "@/api/useApi";
import { ChartCard } from "@/charts/ChartCard";
import { EChart } from "@/charts/EChart";
import { toMessageQuery, useOpenMessages } from "@/lib/drilldown";
import { Segmented } from "@/components/Segmented";
import { isEmptySeries } from "@/features/overview/tables";
import {
  bumpOption,
  contactsOption,
  graphOption,
  streamOption,
} from "@/features/overview/transforms";
import type { ContactMetric } from "@/features/overview/types";

const METRICS: ReadonlyArray<{ value: ContactMetric; label: string }> = [
  { value: "messages", label: "Messages" },
  { value: "words", label: "Words" },
  { value: "emojis", label: "Emojis" },
  { value: "calls", label: "Calls" },
  { value: "reactions", label: "Reactions" },
];

const LIMIT = 20;

/** 3. Top contacts leaderboard. */
export function ContactsCard() {
  const [metric, setMetric] = useState<ContactMetric>("messages");
  const q = useApi("/api/overview/contacts", { query: { metric, limit: LIMIT } });
  const open = useOpenMessages();
  const items = useMemo(() => q.data?.items ?? [], [q.data]);
  const option = useMemo(() => contactsOption(items), [items]);
  return (
    <ChartCard
      title="Top contacts"
      info="Your chats ranked by the chosen metric (a 1:1 chat stands for that person; group chats count as one entry). Click a bar to open that chat's messages."
      query={q}
      empty={q.isSuccess && items.length === 0}
      data={{
        headers: ["chat", "kind", metric, "share"],
        rows: items.map((i) => [i.chat.title, i.chat.kind, i.value, i.share]),
      }}
      actions={<Segmented label="Metric" value={metric} options={METRICS} onChange={setMetric} />}
      height={Math.max(240, items.length * 24 + 16)}
    >
      <EChart
        option={option}
        height={Math.max(240, items.length * 24 + 16)}
        onEvents={{
          click: (p: { dataIndex?: number }) => {
            const it = p.dataIndex == null ? undefined : items[p.dataIndex];
            if (it) open(toMessageQuery(it.drilldown));
          },
        }}
      />
    </ChartCard>
  );
}

type TopView = "bump" | "stream";

/** 4. Top contacts over time: bump chart (rank per month) or stream graph. */
export function TopOverTimeCard() {
  const [view, setView] = useState<TopView>("bump");
  const q = useApi("/api/overview/top-over-time");
  const open = useOpenMessages();
  const d = q.data;
  const option = useMemo(() => {
    if (!d) return {};
    return view === "bump" ? bumpOption(d.ranks, d.chats) : streamOption(d.stream, d.chats);
  }, [d, view]);
  return (
    <ChartCard
      title="Top contacts over time"
      info="Bump chart: each month's rank of your overall top chats among all chats (1 = most messages that month; ranks below 15 are hidden). Stream graph: their monthly message counts. Click a point to open that chat's messages in that month."
      query={q}
      empty={q.isSuccess && isEmptySeries(d?.stream)}
      data={timeSeriesTable(view === "bump" ? d?.ranks : d?.stream, "month")}
      actions={
        <Segmented
          label="View"
          value={view}
          options={[
            { value: "bump", label: "Rank" },
            { value: "stream", label: "Stream" },
          ]}
          onChange={setView}
        />
      }
      className="xl:col-span-2"
    >
      <EChart
        option={option}
        height={380}
        onEvents={{
          click: (p: { seriesId?: string; dataIndex?: number }) => {
            if (!d || view !== "bump" || p.dataIndex == null || !p.seriesId) return;
            const x = d.ranks.x[p.dataIndex];
            if (!x) return;
            const end = new Date(Date.UTC(Number(x.slice(0, 4)), Number(x.slice(5, 7)), 0));
            open({ thread_id: p.seriesId, start: x, end: end.toISOString().slice(0, 10) });
          },
        }}
      />
    </ChartCard>
  );
}

/** 16. Social graph: people connected by shared group chats. */
export function GraphCard() {
  const q = useApi("/api/overview/graph");
  const open = useOpenMessages();
  const g = q.data;
  const option = useMemo(() => (g ? graphOption(g) : {}), [g]);
  return (
    <ChartCard
      title="Social graph"
      info="People connected by the group chats you share with them. Node size = messages they sent; line width = number of shared group chats. Drag to rearrange, scroll to zoom. Click a person or a line to open the messages behind it."
      query={q}
      empty={q.isSuccess && (g?.nodes.length ?? 0) === 0}
      emptyMessage="No group chats in this range."
      data={
        g
          ? {
              headers: ["source", "target", "shared group chats"],
              rows: g.edges.map((e) => {
                const name = (id: number) =>
                  g.nodes.find((n) => n.person.person_id === id)?.person.name ?? String(id);
                return [name(e.source), name(e.target), e.weight];
              }),
            }
          : null
      }
      className="xl:col-span-2"
    >
      <EChart
        option={option}
        height={480}
        onEvents={{
          click: (p: { dataType?: string; dataIndex?: number; data?: { id?: string } }) => {
            if (!g || p.dataIndex == null) return;
            if (p.dataType === "edge") {
              const e = g.edges[p.dataIndex];
              if (e) open(toMessageQuery(e.drilldown));
            } else if (p.data?.id) {
              open({ sender_id: p.data.id });
            }
          },
        }}
      />
    </ChartCard>
  );
}
