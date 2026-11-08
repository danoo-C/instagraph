import { categoryTable } from "@/charts/tables";
import { useMemo } from "react";
import { useApi } from "@/api/useApi";
import { ChartCard } from "@/charts/ChartCard";
import { EChart } from "@/charts/EChart";
import { DataTable } from "@/components/DataTable";
import { dataTableColumnHelper } from "@/components/dataTableColumns";
import { ErrorCard } from "@/components/ErrorCard";
import { PersonBadge } from "@/components/PersonBadge";
import { Skeleton } from "@/components/Skeleton";
import { toMessageQuery, useOpenMessages } from "@/lib/drilldown";

import { emojiBarsOption, formatMs, sizeOption } from "@/features/overview/transforms";
import type { ApiAward, ApiYearRow } from "@/features/overview/types";
import { OTHERS_COLOR, personColor } from "@/lib/colors";
import { formatNumber, formatPercent } from "@/lib/format";

/** 13. Top emojis overall and mine. */
export function EmojisCard() {
  const q = useApi("/api/overview/emojis");
  const open = useOpenMessages();
  const d = q.data;
  const panels = useMemo(
    () =>
      d
        ? [
            { title: "Everyone", items: d.top, color: OTHERS_COLOR },
            { title: "Mine", items: d.mine, color: personColor(0) },
          ]
        : [],
    [d],
  );
  const option = useMemo(() => emojiBarsOption(panels), [panels]);
  const rows = Math.min(15, Math.max(d?.top.length ?? 0, d?.mine.length ?? 0));
  const height = Math.max(240, rows * 26 + 40);
  return (
    <ChartCard
      title="Top emojis"
      info={`Most used emojis in message text: everyone vs only yours (system messages excluded). ${
        d ? `${formatNumber(d.total)} emojis, ${formatNumber(d.unique)} different.` : ""
      } Click a bar to open messages with that emoji.`}
      query={q}
      empty={q.isSuccess && (d?.top.length ?? 0) === 0}
      data={
        d
          ? {
              headers: ["list", "emoji", "count"],
              rows: [
                ...d.top.map((e) => ["everyone", e.emoji, e.count]),
                ...d.mine.map((e) => ["mine", e.emoji, e.count]),
              ],
            }
          : null
      }
      height={height}
    >
      <EChart
        option={option}
        height={height}
        onEvents={{
          click: (p: { seriesIndex?: number; dataIndex?: number }) => {
            const panel = panels[p.seriesIndex ?? -1];
            const e = p.dataIndex == null ? undefined : panel?.items[p.dataIndex];
            if (e) open(e.drilldown ? toMessageQuery(e.drilldown) : { emoji: e.emoji });
          },
        }}
      />
    </ChartCard>
  );
}

/** 14. Chats by size (log bins). */
export function SizeDistributionCard() {
  const q = useApi("/api/overview/size-distribution");
  const open = useOpenMessages();
  const chart = q.data?.chart;
  const option = useMemo(() => (chart ? sizeOption(chart) : {}), [chart]);
  return (
    <ChartCard
      title="Chats by size"
      info="How many chats have 1, 2–4, 5–9, … messages (logarithmic bins). Most people have a few huge chats and a long tail of tiny ones. Click a bar to open the messages of those chats."
      query={q}
      empty={q.isSuccess && (q.data?.bins.length ?? 0) === 0}
      data={categoryTable(chart, "messages per chat")}
    >
      <EChart
        option={option}
        onEvents={{
          click: (p: { dataIndex?: number }) => {
            const b = p.dataIndex == null ? undefined : q.data?.bins[p.dataIndex];
            if (b) open(toMessageQuery(b.drilldown));
          },
        }}
      />
    </ChartCard>
  );
}

const col = dataTableColumnHelper<ApiYearRow>();
const yearColumns = col.columns([
  col.accessor("year", { header: "Year" }),
  col.accessor("messages", { header: "Messages", cell: (c) => formatNumber(c.getValue()) }),
  col.accessor("sent", { header: "Sent", cell: (c) => formatNumber(c.getValue()) }),
  col.accessor("received", { header: "Received", cell: (c) => formatNumber(c.getValue()) }),
  col.accessor("people", { header: "People", cell: (c) => formatNumber(c.getValue()) }),
  col.accessor("chats", { header: "Chats", cell: (c) => formatNumber(c.getValue()) }),
  col.accessor("active_days", { header: "Active days", cell: (c) => formatNumber(c.getValue()) }),
  col.accessor((r) => r.top_chat?.title ?? "", {
    id: "top_chat",
    header: "Top contact",
    cell: (c) => {
      const r = c.row.original;
      if (!r.top_chat) return "—";
      return (
        <span className="flex items-center gap-1.5">
          {r.top_chat.person && (
            <PersonBadge
              id={r.top_chat.person.person_id}
              name={r.top_chat.person.name}
              color_index={r.top_chat.person.color_index}
              size="xs"
            />
          )}
          <span className="truncate">{r.top_chat.title}</span>
          <span className="text-xs text-muted">({formatNumber(r.top_chat_messages)})</span>
        </span>
      );
    },
  }),
  col.accessor((r) => r.top_emoji ?? "", {
    id: "top_emoji",
    header: "Top emoji",
    cell: (c) => {
      const r = c.row.original;
      return r.top_emoji ? (
        <span>
          <span className="text-lg">{r.top_emoji}</span>{" "}
          <span className="text-xs text-muted">({formatNumber(r.top_emoji_count)})</span>
        </span>
      ) : (
        "—"
      );
    },
  }),
]);

/** 17. Year-over-year comparison table. */
export function YearsCard() {
  const q = useApi("/api/overview/years");
  const open = useOpenMessages();
  const years = useMemo(() => q.data?.years ?? [], [q.data]);
  return (
    <ChartCard
      title="Year by year"
      info="Messages, people, top contact and top emoji per calendar year. Click a row to open that year's messages."
      query={q}
      empty={q.isSuccess && years.length === 0}
      height={240}
      data={{
        headers: [
          "year",
          "messages",
          "sent",
          "received",
          "people",
          "chats",
          "active_days",
          "top_chat",
          "top_chat_messages",
          "top_emoji",
          "top_emoji_count",
        ],
        rows: years.map((y) => [
          y.year,
          y.messages,
          y.sent,
          y.received,
          y.people,
          y.chats,
          y.active_days,
          y.top_chat?.title ?? "",
          y.top_chat_messages,
          y.top_emoji ?? "",
          y.top_emoji_count,
        ]),
      }}
      className="xl:col-span-2"
    >
      <DataTable
        data={years}
        columns={yearColumns}
        getRowId={(r) => String(r.year)}
        initialSorting={[{ id: "year", desc: true }]}
        onRowClick={(r) => open(toMessageQuery(r.drilldown))}
        caption="Year by year"
      />
    </ChartCard>
  );
}

function awardValue(a: ApiAward, v: number | null | undefined): string {
  if (v == null) return "—";
  switch (a.unit) {
    case "ratio":
      return formatPercent(v);
    case "ms":
      return formatMs(v);
    case "per_message":
      return `${formatNumber(v, { maximumFractionDigits: 2 })} / msg`;
    default:
      return formatNumber(v);
  }
}

/** Global awards (fun superlatives across all chats). */
export function AwardsSection() {
  const q = useApi("/api/overview/awards");
  const open = useOpenMessages();
  if (q.error) return <ErrorCard error={q.error} onRetry={() => void q.refetch()} />;
  const awards = q.data?.awards ?? [];
  return (
    <section aria-labelledby="awards-title" className="flex flex-col gap-3">
      <h2 id="awards-title" className="text-lg font-semibold">
        Awards
      </h2>
      {q.isLoading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      ) : awards.length === 0 ? (
        <p className="text-sm text-muted">No awards for this range.</p>
      ) : (
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
          {awards.map((a) => {
            const clickable = Boolean(a.drilldown) && a.winners.length > 0;
            const body = (
              <>
                <div className="flex items-center gap-2">
                  <span className="text-2xl" aria-hidden>
                    {a.emoji}
                  </span>
                  <span className="font-semibold">{a.title}</span>
                </div>
                <p className="text-xs text-muted">{a.rule}</p>
                {a.winners.length ? (
                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    {a.winners.map((w) => (
                      <PersonBadge
                        key={w.person_id}
                        id={w.person_id}
                        name={w.name}
                        color_index={w.color_index}
                        size="sm"
                        showName
                      />
                    ))}
                    <span className="ml-auto text-sm font-medium tabular-nums">
                      {awardValue(a, a.value)}
                    </span>
                  </div>
                ) : (
                  <p className="mt-1 text-sm text-muted">Nobody qualifies ({a.eligibility})</p>
                )}
                {a.ranking.length > 1 && (
                  <ol className="mt-1 text-xs text-muted">
                    {a.ranking.slice(1, 4).map((r, i) => (
                      <li key={r.person.person_id} className="flex justify-between gap-2">
                        <span className="truncate">
                          {i + 2}. {r.person.name}
                        </span>
                        <span className="tabular-nums">{awardValue(a, r.value)}</span>
                      </li>
                    ))}
                  </ol>
                )}
              </>
            );
            return (
              <li key={a.key}>
                {clickable ? (
                  <button
                    type="button"
                    title={`${a.rule} — eligibility: ${a.eligibility}`}
                    onClick={() => open(toMessageQuery(a.drilldown))}
                    className="card flex h-full w-full flex-col gap-1 p-3 text-left transition-colors hover:border-accent/60 hover:bg-surface-2"
                  >
                    {body}
                  </button>
                ) : (
                  <div
                    className="card flex h-full flex-col gap-1 p-3"
                    title={`Eligibility: ${a.eligibility}`}
                  >
                    {body}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
