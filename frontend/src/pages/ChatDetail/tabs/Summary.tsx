import { categoryTable } from "@/charts/tables";
import { useMemo } from "react";
import { useApi } from "@/api/useApi";
import { ChartCard } from "@/charts/ChartCard";
import { recordsToTable } from "@/charts/csv";
import { EChart } from "@/charts/EChart";
import type { EChartsOption } from "@/charts/registerEcharts";
import { DataTable } from "@/components/DataTable";
import { dataTableColumnHelper } from "@/components/dataTableColumns";
import { ErrorCard } from "@/components/ErrorCard";
import { PersonBadge } from "@/components/PersonBadge";
import { Tile, TileGrid } from "@/components/Tile";
import { formatMs } from "@/features/chat/charts";
import { parseChatId } from "@/features/chat/context";
import { toMessageQuery, useDrill } from "@/lib/drilldown";
import type { ApiCategoryChart, ApiTile, ChatPersonRow, ChatSummary } from "@/features/chat/types";
import { MessageQuote } from "@/features/chat/ui";
import { personColor } from "@/lib/colors";
import { formatDate, formatNumber, formatPercent, type ValueFormat } from "@/lib/format";
import type { ChatTabProps } from "@/pages/ChatDetail/tabs";

/** Backend tile → Tile props (units: ms → seconds, ratio → percent, date → formatted). */
function tileProps(t: ApiTile): { value: number | string | null; format: ValueFormat } {
  const v = t.value;
  if (typeof v === "boolean") return { value: v ? "Yes" : "No", format: "text" };
  if (v == null) return { value: null, format: "number" };
  switch (t.unit) {
    case "ms":
      return { value: typeof v === "number" ? v / 1000 : v, format: "duration" };
    case "s":
      return { value: v, format: "duration" };
    case "ratio":
      return { value: v, format: "percent" };
    case "date":
      return { value: formatDate(String(v)), format: "text" };
    case "datetime":
      return { value: formatDate(String(v)), format: "text" };
    case "per_day":
    case "per_message":
      return { value: typeof v === "number" ? Math.round(v * 100) / 100 : v, format: "number" };
    default:
      return { value: v, format: typeof v === "number" ? "number" : "text" };
  }
}

const col = dataTableColumnHelper<ChatPersonRow>();

function useColumns() {
  const open = useDrill();
  return useMemo(() => {
    const count = (
      key: keyof ChatPersonRow & string,
      header: string,
      drill?: (r: ChatPersonRow) => Record<string, unknown>,
    ) =>
      col.accessor((r) => r[key] as number, {
        id: key,
        header,
        cell: (c) => {
          const r = c.row.original;
          return (
            <button
              type="button"
              className="tabular-nums hover:text-accent hover:underline"
              onClick={(e) => {
                e.stopPropagation();
                open({ ...r.drilldown, ...(drill?.(r) ?? {}) });
              }}
            >
              {formatNumber(c.getValue())}
            </button>
          );
        },
      });
    const plain = (
      key: keyof ChatPersonRow & string,
      header: string,
      fmt: (v: number | null) => string,
    ) =>
      col.accessor((r) => (r[key] as number | null) ?? null, {
        id: key,
        header,
        sortUndefined: "last",
        cell: (c) => <span className="tabular-nums">{fmt(c.getValue())}</span>,
      });
    const dec = (v: number | null) => formatNumber(v, { maximumFractionDigits: 2 });
    return col.columns([
      col.accessor((r) => r.person.name, {
        id: "person",
        header: "Person",
        cell: (c) => {
          const p = c.row.original.person;
          return (
            <PersonBadge
              name={p.is_owner ? `${p.name} (me)` : p.name}
              id={p.person_id}
              color_index={p.color_index}
              showName
            />
          );
        },
      }),
      count("messages", "Messages"),
      plain("share", "Share", (v) => formatPercent(v)),
      plain("words", "Words", (v) => formatNumber(v)),
      plain("avg_words_per_message", "Words/msg", dec),
      plain("characters", "Characters", (v) => formatNumber(v)),
      plain("emojis", "Emojis", (v) => formatNumber(v)),
      plain("emojis_per_message", "Emojis/msg", dec),
      plain("reactions_given", "Reactions given", (v) => formatNumber(v)),
      count("reactions_received", "Reactions received", () => ({ has_reactions: true })),
      count("photos", "Photos", () => ({ types: ["photo"] })),
      count("videos", "Videos", () => ({ types: ["video"] })),
      count("voice_notes", "Voice notes", () => ({ types: ["audio"] })),
      count("reels", "Reels", () => ({ share_kind: "reel" })),
      count("links", "Links", () => ({ types: ["link"] })),
      count("calls_started", "Calls started", () => ({ types: ["call"] })),
      count("unsent", "Unsent", () => ({ types: ["unsent"] })),
      plain("median_reply_ms", "Median reply", (v) => formatMs(v)),
      plain("initiation_rate", "Initiations", (v) => formatPercent(v)),
    ]);
  }, [open]);
}

function donutOption(chart: ApiCategoryChart, people: ChatPersonRow[]): EChartsOption {
  const s = chart.series[0];
  const byName = new Map(people.map((p) => [p.person.name, p.person]));
  return {
    tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
    legend: { type: "scroll", bottom: 0 },
    series: [
      {
        type: "pie",
        radius: ["45%", "72%"],
        center: ["50%", "45%"],
        avoidLabelOverlap: true,
        label: { formatter: "{b}\n{d}%" },
        data: chart.categories.map((c, i) => {
          const p = byName.get(c);
          return {
            name: c,
            value: s?.values[i] ?? 0,
            itemStyle: {
              color: personColor(p ? { id: p.person_id, color_index: p.color_index } : c),
            },
          };
        }),
      },
    ],
  };
}

export default function SummaryTab({ chatId }: ChatTabProps) {
  const q = useApi("/api/chats/{chat_id}/summary", { path: { chat_id: parseChatId(chatId) } });
  const open = useDrill();
  const columns = useColumns();
  const d: ChatSummary | undefined = q.data;

  if (q.error) return <ErrorCard error={q.error} onRetry={() => void q.refetch()} />;

  const people = d?.people ?? [];
  const donutClick = (chart: ApiCategoryChart | undefined) => ({
    click: (p: { dataIndex: number }) => open(chart?.drilldowns?.[p.dataIndex]),
  });

  return (
    <div className="flex flex-col gap-4">
      <TileGrid>
        {d
          ? d.tiles.map((t) => {
              const { value, format } = tileProps(t);
              return (
                <Tile
                  key={t.key}
                  label={t.label}
                  value={value}
                  format={format}
                  hint={t.detail ?? undefined}
                  query={t.drilldown ? toMessageQuery(t.drilldown) : undefined}
                />
              );
            })
          : Array.from({ length: 12 }, (_, i) => <Tile key={i} label="…" value={null} loading />)}
        {d?.mutual_streak && d.mutual_streak.length_days > 0 && (
          <Tile
            label="Mutual streak (days)"
            value={d.mutual_streak.length_days}
            hint={`${formatDate(d.mutual_streak.start)} – ${formatDate(d.mutual_streak.end)}`}
            query={
              d.mutual_streak.drilldown ? toMessageQuery(d.mutual_streak.drilldown) : undefined
            }
          />
        )}
        {d && d.current_streak.length_days > 0 && (
          <Tile
            label="Current streak (days)"
            value={d.current_streak.length_days}
            hint={`since ${formatDate(d.current_streak.start)}`}
            query={
              d.current_streak.drilldown ? toMessageQuery(d.current_streak.drilldown) : undefined
            }
          />
        )}
      </TileGrid>

      <ChartCard
        title="Per person"
        info="Every person's numbers in this chat. Click a column header to sort, and a count to open those messages."
        query={q}
        height={160}
        empty={q.isSuccess && people.length === 0}
        data={recordsToTable(
          people.map((r) => ({
            person: r.person.name,
            messages: r.messages,
            share: r.share,
            words: r.words,
            avg_words_per_message: r.avg_words_per_message,
            characters: r.characters,
            emojis: r.emojis,
            emojis_per_message: r.emojis_per_message,
            reactions_given: r.reactions_given,
            reactions_received: r.reactions_received,
            photos: r.photos,
            videos: r.videos,
            voice_notes: r.voice_notes,
            reels: r.reels,
            links: r.links,
            calls_started: r.calls_started,
            unsent: r.unsent,
            median_reply_s: r.median_reply_ms == null ? null : r.median_reply_ms / 1000,
            initiation_rate: r.initiation_rate,
          })),
        )}
      >
        <DataTable
          data={people}
          columns={columns}
          getRowId={(r) => String(r.person.person_id)}
          initialSorting={[{ id: "messages", desc: true }]}
          caption="Per-person breakdown"
          virtualize={people.length > 30 ? { height: 520 } : undefined}
        />
      </ChartCard>

      <div className="card-grid">
        <ChartCard
          title="Messages per person"
          info="Share of all messages sent by each person. Click a slice to read their messages."
          query={q}
          empty={q.isSuccess && !d?.messages_by_person.categories.length}
          data={d ? categoryTable(d.messages_by_person, "Person") : null}
        >
          {d && (
            <EChart
              option={donutOption(d.messages_by_person, people)}
              onEvents={donutClick(d.messages_by_person)}
              height={300}
            />
          )}
        </ChartCard>
        <ChartCard
          title="Words per person"
          info="Share of all words written by each person (text messages, URLs removed)."
          query={q}
          empty={q.isSuccess && !d?.words_by_person.categories.length}
          data={d ? categoryTable(d.words_by_person, "Person") : null}
        >
          {d && (
            <EChart
              option={donutOption(d.words_by_person, people)}
              onEvents={donutClick(d.words_by_person)}
              height={300}
            />
          )}
        </ChartCard>
      </div>

      <div className="card-grid">
        <section className="card flex flex-col gap-3 p-4" aria-label="First and last message">
          <h3 className="text-sm font-semibold">First and most recent message</h3>
          <MessageQuote message={d?.first_message} label="First message ever" />
          <MessageQuote message={d?.last_message} label="Most recent message" />
        </section>
        {d?.longest_silence && (
          <section className="card flex flex-col gap-3 p-4" aria-label="Longest silence">
            <h3 className="text-sm font-semibold">
              Longest silence: {formatMs(d.longest_silence.gap_ms)}
            </h3>
            <MessageQuote message={d.longest_silence.before} label="Before" />
            <MessageQuote message={d.longest_silence.after} label="After" />
          </section>
        )}
      </div>
    </div>
  );
}
