import { categoryTable, heatmapTable, timeSeriesTable } from "@/charts/tables";
import { useMemo, useState } from "react";
import { useApi } from "@/api/useApi";
import type { components } from "@/api/schema";
import { ChartCard } from "@/charts/ChartCard";
import { recordsToTable } from "@/charts/csv";
import { EChart } from "@/charts/EChart";
import { defaultGrid, timeSeriesZoom } from "@/charts/sampling";
import { DataTable } from "@/components/DataTable";
import { dataTableColumnHelper } from "@/components/dataTableColumns";
import { MessageBubble } from "@/components/MessageBubble";
import { PersonBadge } from "@/components/PersonBadge";
import { TileGrid } from "@/components/Tile";
import {
  bucketEnd,
  isEmptyChart,
  personRefColor,
  seriesColor,
  timeSeriesToEcharts,
} from "@/features/tabs/charts";
import { heatmapOption } from "@/features/tabs/callsHelpers";
import { ApiTileView } from "@/features/tabs/callsTile";
import { useDrill, useOpenContext } from "@/lib/drilldown";
import { SelectInput } from "@/components/SelectInput";
import { TabState } from "@/features/tabs/ui";
import { formatPercent } from "@/lib/format";
import type { ChatTabProps } from "@/pages/ChatDetail/tabs";
import { useFilters } from "@/state/filters";
import { useResolvedTheme } from "@/state/theme";

type ReactionsResponse = components["schemas"]["ReactionsResponse"];
type ReciprocityPair = components["schemas"]["ReciprocityPair"];

const recCol = dataTableColumnHelper<ReciprocityPair>();

export default function ReactionsTab({ chatId }: ChatTabProps) {
  const q = useApi("/api/chats/{chat_id}/reactions", { path: { chat_id: Number(chatId) } });
  return (
    <TabState query={q}>{() => <ReactionsBody data={q.data!} chatId={Number(chatId)} />}</TabState>
  );
}

function ReactionsBody({ data, chatId }: { data: ReactionsResponse; chatId: number }) {
  const drill = useDrill();
  const openContext = useOpenContext();
  const theme = useResolvedTheme();
  const { filters } = useFilters();
  const [emojiScope, setEmojiScope] = useState("all");
  const threadQ = { thread_id: chatId };

  const gr = data.given_received;
  const people = data.people;

  const emojiList = useMemo(() => {
    if (emojiScope === "all") return data.top_emojis;
    const p = people.find((x) => String(x.person.person_id) === emojiScope);
    return p?.top_given ?? [];
  }, [emojiScope, data.top_emojis, people]);
  const scopePerson = people.find((x) => String(x.person.person_id) === emojiScope)?.person;

  const recColumns = useMemo(
    () =>
      recCol.columns([
        recCol.accessor((r) => `${r.a.name} ↔ ${r.b.name}`, {
          id: "pair",
          header: "Pair",
          cell: (c) => (
            <span className="inline-flex items-center gap-1">
              <PersonBadge
                name={c.row.original.a.name}
                id={c.row.original.a.person_id}
                color_index={c.row.original.a.color_index}
                showName
              />
              <span aria-hidden="true">↔</span>
              <PersonBadge
                name={c.row.original.b.name}
                id={c.row.original.b.person_id}
                color_index={c.row.original.b.color_index}
                showName
              />
            </span>
          ),
        }),
        recCol.accessor("a_to_b", { header: "A → B" }),
        recCol.accessor("b_to_a", { header: "B → A" }),
        recCol.accessor("reciprocity", {
          header: "Reciprocity",
          cell: (c) => formatPercent(c.getValue()),
        }),
      ]),
    [],
  );

  const matrixPeople = data.matrix_people;

  return (
    <div className="flex flex-col gap-4">
      <TileGrid>
        {data.tiles.map((t) => (
          <ApiTileView key={t.key} tile={t} timeZone={filters.tz} />
        ))}
      </TileGrid>

      <div className="card-grid">
        <ChartCard
          title="Reactions given and received"
          info="Reactions each person gave to others' messages and received on their own messages."
          data={categoryTable(gr, "person")}
          empty={isEmptyChart(gr)}
        >
          <EChart
            option={{
              grid: defaultGrid({ legend: true }),
              legend: { top: 0 },
              tooltip: { trigger: "axis" },
              xAxis: { type: "category", data: gr.categories },
              yAxis: { type: "value", minInterval: 1 },
              series: gr.series.map((s, i) => ({
                name: s.name,
                type: "bar" as const,
                itemStyle: { color: seriesColor(s, i) },
                data: s.values.map((v, j) => ({
                  value: v,
                  itemStyle: {
                    color: people[j] ? personRefColor(people[j].person) : seriesColor(s, i),
                    opacity: s.key === "given" ? 1 : 0.55,
                  },
                })),
              })),
            }}
            onEvents={{
              click: (p: { dataIndex: number; seriesIndex: number }) => {
                const row = people[p.dataIndex];
                if (!row) return;
                const key = gr.series[p.seriesIndex]?.key;
                drill(key === "given" ? row.given_drilldown : row.received_drilldown);
              },
            }}
          />
        </ChartCard>

        <ChartCard
          title="Top reaction emojis"
          info="Most used reaction emojis, overall or given by one person. Click a bar to see the reacted messages."
          data={recordsToTable(emojiList, ["emoji", "count"])}
          empty={emojiList.length === 0}
          actions={
            <SelectInput label="Reactions by" value={emojiScope} onChange={setEmojiScope}>
              <option value="all">Everyone</option>
              {people.map((p) => (
                <option key={p.person.person_id} value={String(p.person.person_id)}>
                  Given by {p.person.name}
                </option>
              ))}
            </SelectInput>
          }
        >
          <EChart
            height={Math.max(200, emojiList.length * 28 + 40)}
            option={{
              grid: defaultGrid(),
              tooltip: { trigger: "axis" },
              xAxis: { type: "value", minInterval: 1 },
              yAxis: {
                type: "category",
                inverse: true,
                data: emojiList.map((e) => e.emoji),
                axisLabel: { fontSize: 16 },
              },
              series: [
                {
                  type: "bar",
                  name: "Reactions",
                  itemStyle: {
                    color: scopePerson ? personRefColor(scopePerson) : seriesColor({ key: "x" }, 0),
                  },
                  label: { show: true, position: "right" },
                  data: emojiList.map((e) => e.count),
                },
              ],
            }}
            onEvents={{
              click: (p: { dataIndex: number }) => drill(emojiList[p.dataIndex]?.drilldown),
            }}
          />
        </ChartCard>

        <ChartCard
          title="Who reacts to whom"
          info="Rows: who reacted. Columns: whose message got the reaction. Click a cell to see those messages."
          data={heatmapTable(data.matrix, "reactor \\ author")}
          empty={matrixPeople.length < 2}
          emptyMessage="Not enough people reacted to show a matrix"
        >
          <EChart
            height={Math.max(260, matrixPeople.length * 36 + 100)}
            option={heatmapOption(data.matrix, theme, { valueLabel: "Reactions" })}
            onEvents={{
              click: (p: { value: [number, number, number] }) => {
                const actor = matrixPeople[p.value[1]];
                const target = matrixPeople[p.value[0]];
                if (actor && target)
                  drill({
                    thread_id: chatId,
                    reactor_id: actor.person_id,
                    sender_id: target.person_id,
                  });
              },
            }}
          />
        </ChartCard>

        <ChartCard
          title="Reactions over time"
          info="Reactions given per person per month (by reaction time when known, else message time)."
          data={timeSeriesTable(data.over_time)}
          empty={isEmptyChart(data.over_time)}
        >
          <EChart
            option={{
              grid: defaultGrid({ legend: true, slider: true }),
              legend: { top: 0, type: "scroll" },
              tooltip: { trigger: "axis" },
              dataZoom: timeSeriesZoom(),
              xAxis: { type: "time" },
              yAxis: { type: "value", minInterval: 1 },
              series: timeSeriesToEcharts(data.over_time),
            }}
            onEvents={{
              click: (p: { value: [string, number]; seriesIndex: number }) => {
                const s = data.over_time.series[p.seriesIndex];
                drill(
                  {
                    thread_id: chatId,
                    ...(s?.person_id != null ? { reactor_id: s.person_id } : {}),
                  },
                  { start: p.value[0], end: bucketEnd(p.value[0], data.over_time.bucket) },
                );
              },
            }}
          />
        </ChartCard>
      </div>

      <ChartCard
        title="Most reacted messages"
        info="Messages with the most reactions. Click one to see it in its thread."
        data={recordsToTable(
          data.most_reacted.map((m) => ({
            ts: m.message.ts,
            sender: m.message.sender.name,
            text: m.message.text ?? "",
            reactions: m.count,
            reactors: m.reactors.map((r) => `${r.person.name} ${r.emoji}`).join("; "),
          })),
        )}
        empty={data.most_reacted.length === 0}
        height={200}
      >
        <ol className="flex max-h-[32rem] flex-col gap-3 overflow-auto pr-1">
          {data.most_reacted.map((m) => (
            <li key={m.message.message_id} className="flex items-start gap-2">
              <span className="w-10 shrink-0 pt-6 text-right text-sm font-semibold tabular-nums text-muted">
                {m.count}×
              </span>
              <MessageBubble
                className="min-w-0 flex-1"
                text={
                  m.message.text ?? (m.message.type === "text" ? "(hidden)" : `[${m.message.type}]`)
                }
                senderName={m.message.sender.name}
                senderId={m.message.sender.person_id}
                senderColorIndex={m.message.sender.color_index}
                timestamp={m.message.ts}
                timeZone={filters.tz}
                reactions={m.reactors.map((r) => ({ emoji: r.emoji, actor: r.person.name }))}
                onClick={() => openContext(m.message.message_id, m.message.thread_id)}
              />
            </li>
          ))}
        </ol>
      </ChartCard>

      {data.reciprocity.length > 0 && (
        <ChartCard
          title="Reaction reciprocity"
          info="min(A→B, B→A) / max(A→B, B→A): 100% means both react to each other equally often."
          data={recordsToTable(
            data.reciprocity.map((r) => ({
              a: r.a.name,
              b: r.b.name,
              a_to_b: r.a_to_b,
              b_to_a: r.b_to_a,
              reciprocity: r.reciprocity,
            })),
          )}
        >
          <DataTable
            data={data.reciprocity}
            columns={recColumns}
            initialSorting={[{ id: "reciprocity", desc: true }]}
            onRowClick={(r) =>
              drill({ ...threadQ, reactor_id: r.a.person_id, sender_id: r.b.person_id })
            }
            caption="Reaction reciprocity"
          />
        </ChartCard>
      )}
    </div>
  );
}
