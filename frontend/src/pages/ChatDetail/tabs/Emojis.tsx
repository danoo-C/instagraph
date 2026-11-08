import { categoryTable, timeSeriesTable } from "@/charts/tables";
import { useApi } from "@/api/useApi";
import type { components } from "@/api/schema";
import { ChartCard } from "@/charts/ChartCard";
import { recordsToTable } from "@/charts/csv";
import { EChart } from "@/charts/EChart";
import { defaultGrid, timeSeriesZoom } from "@/charts/sampling";
import { PersonBadge } from "@/components/PersonBadge";
import { Tile, TileGrid } from "@/components/Tile";
import {
  bucketEnd,
  isEmptyChart,
  personRefColor,
  seriesColor,
  timeSeriesToEcharts,
} from "@/features/tabs/charts";
import { toMessageQuery, useDrill, useOpenContext } from "@/lib/drilldown";
import { SectionTitle, TabState } from "@/features/tabs/ui";
import type { TimeSeries } from "@/features/tabs/types";
import { formatDate, formatNumber } from "@/lib/format";
import type { ChatTabProps } from "@/pages/ChatDetail/tabs";
import { useFilters } from "@/state/filters";

type EmojisResponse = components["schemas"]["EmojisResponse"];
type PersonEmojiStats = components["schemas"]["PersonEmojiStats"];
type EmojiChain = components["schemas"]["EmojiChain"];

export default function EmojisTab({ chatId }: ChatTabProps) {
  const q = useApi("/api/chats/{chat_id}/emojis", { path: { chat_id: Number(chatId) } });
  return (
    <TabState query={q}>{() => <EmojisBody data={q.data!} chatId={Number(chatId)} />}</TabState>
  );
}

function EmojisBody({ data, chatId }: { data: EmojisResponse; chatId: number }) {
  const drill = useDrill();
  const openContext = useOpenContext();
  const { filters } = useFilters();
  const threadQ = { thread_id: chatId };

  /** Click on a time-series point → messages in that bucket (optionally for the series' person). */
  const timeClick =
    (ts: TimeSeries, extra?: (key: string) => Record<string, string | number>) =>
    (p: { value: [string, number]; seriesIndex: number }) => {
      const s = ts.series[p.seriesIndex];
      drill(
        { ...threadQ, ...(s?.person_id != null ? { sender_id: s.person_id } : {}) },
        { start: p.value[0], end: bucketEnd(p.value[0], ts.bucket), ...(s ? extra?.(s.key) : {}) },
      );
    };

  const timeOption = (ts: TimeSeries, opts: { stack?: boolean } = {}) => ({
    grid: defaultGrid({ legend: true, slider: true }),
    legend: { top: 0, type: "scroll" as const },
    tooltip: { trigger: "axis" as const },
    dataZoom: timeSeriesZoom(),
    xAxis: { type: "time" as const },
    yAxis: { type: "value" as const, minInterval: 1 },
    series: timeSeriesToEcharts(ts, opts.stack ? { stack: "total", area: true, smooth: true } : {}),
  });

  return (
    <div className="flex flex-col gap-4">
      <TileGrid>
        <Tile label="Emojis" value={data.total} />
        <Tile label="Unique emojis" value={data.unique} />
        <Tile label="Emoji-only messages" value={data.emoji_only_messages} />
        <Tile
          label="Longest emoji chain"
          value={data.longest_chain?.length ?? null}
          hint={
            data.longest_chain
              ? `${data.longest_chain.emojis.slice(0, 40)} · ${data.longest_chain.message.sender.name}`
              : undefined
          }
          onClick={
            data.longest_chain
              ? () => openContext(data.longest_chain!.message.message_id, chatId)
              : undefined
          }
        />
      </TileGrid>

      <SectionTitle>Per person</SectionTitle>
      <div className="grid gap-3 md:grid-cols-2">
        {data.people.map((p) => (
          <PersonEmojiCard
            key={p.person.person_id}
            p={p}
            onChain={(c) => openContext(c.message.message_id, chatId)}
          />
        ))}
      </div>

      <div className="card-grid">
        <ChartCard
          title="Top emojis per person"
          info="Each person's 20 most used emojis. Click a bar to see the messages."
          data={recordsToTable(
            data.people.flatMap((p) =>
              p.top.map((e) => ({ person: p.person.name, emoji: e.emoji, count: e.count })),
            ),
          )}
          empty={data.people.every((p) => p.top.length === 0)}
          className="xl:col-span-2"
        >
          <div
            className="grid gap-4"
            style={{ gridTemplateColumns: `repeat(auto-fit, minmax(220px, 1fr))` }}
          >
            {data.people
              .filter((p) => p.top.length > 0)
              .map((p) => (
                <div key={p.person.person_id} className="min-w-0">
                  <PersonBadge
                    name={p.person.name}
                    id={p.person.person_id}
                    color_index={p.person.color_index}
                    showName
                  />
                  <EChart
                    ariaLabel={`Top emojis of ${p.person.name}`}
                    height={Math.max(160, p.top.length * 22 + 30)}
                    option={{
                      grid: { left: 8, right: 40, top: 8, bottom: 8, containLabel: true },
                      tooltip: { trigger: "axis" },
                      xAxis: { type: "value", show: false },
                      yAxis: {
                        type: "category",
                        inverse: true,
                        data: p.top.map((e) => e.emoji),
                        axisLabel: { fontSize: 15 },
                        axisLine: { show: false },
                      },
                      series: [
                        {
                          type: "bar",
                          name: p.person.name,
                          itemStyle: { color: personRefColor(p.person) },
                          label: { show: true, position: "right" },
                          data: p.top.map((e) => e.count),
                        },
                      ],
                    }}
                    onEvents={{
                      click: (ev: { dataIndex: number }) => {
                        const e = p.top[ev.dataIndex];
                        if (e)
                          drill(
                            e.drilldown ?? {
                              ...threadQ,
                              sender_id: p.person.person_id,
                              emoji: e.emoji,
                            },
                          );
                      },
                    }}
                  />
                </div>
              ))}
          </div>
        </ChartCard>

        <ChartCard
          title="Emoji usage over time"
          info="Emojis sent per period, per person."
          data={timeSeriesTable(data.usage_over_time)}
          empty={isEmptyChart(data.usage_over_time)}
        >
          <EChart
            option={timeOption(data.usage_over_time)}
            onEvents={{ click: timeClick(data.usage_over_time) }}
          />
        </ChartCard>

        <ChartCard
          title="Top 10 emojis over time"
          info="Stream of the 10 most used emojis in this chat, stacked per period."
          data={timeSeriesTable(data.top_over_time)}
          empty={isEmptyChart(data.top_over_time)}
        >
          <EChart
            option={timeOption(data.top_over_time, { stack: true })}
            onEvents={{
              click: (p: { value: [string, number]; seriesIndex: number }) => {
                const s = data.top_over_time.series[p.seriesIndex];
                drill(threadQ, {
                  start: p.value[0],
                  end: bucketEnd(p.value[0], data.top_over_time.bucket),
                  ...(s ? { emoji: s.name || s.key } : {}),
                });
              },
            }}
          />
        </ChartCard>

        <ChartCard
          title="Heart emojis over time"
          info="Heart emojis (❤️🧡💛💚💙💜🖤🤍🤎💕💖…) sent per period, per person."
          data={timeSeriesTable(data.hearts_over_time)}
          empty={isEmptyChart(data.hearts_over_time)}
        >
          <EChart
            option={timeOption(data.hearts_over_time)}
            onEvents={{ click: timeClick(data.hearts_over_time) }}
          />
        </ChartCard>

        <ChartCard
          title="Emoji categories"
          info="Emojis per Unicode category (smileys, hearts, animals, food, …), stacked by person."
          data={categoryTable(data.categories, "category")}
          empty={isEmptyChart(data.categories)}
        >
          <EChart
            option={{
              grid: defaultGrid({ legend: true }),
              legend: { top: 0, type: "scroll" },
              tooltip: { trigger: "axis" },
              xAxis: {
                type: "category",
                data: data.categories.categories,
                axisLabel: { interval: 0, rotate: 30 },
              },
              yAxis: { type: "value", minInterval: 1 },
              series: data.categories.series.map((s, i) => ({
                name: s.name,
                type: "bar" as const,
                stack: "total",
                itemStyle: { color: seriesColor(s, i) },
                data: s.values,
              })),
            }}
          />
        </ChartCard>

        <ChartCard
          title="Text emoticons"
          info="Classic emoticons like :) <3 xD, counted separately from emojis."
          data={categoryTable(data.emoticons, "emoticon")}
          empty={isEmptyChart(data.emoticons)}
          emptyMessage="No emoticons"
        >
          <EChart
            option={{
              grid: defaultGrid({ legend: true }),
              legend: { top: 0, type: "scroll" },
              tooltip: { trigger: "axis" },
              xAxis: { type: "category", data: data.emoticons.categories },
              yAxis: { type: "value", minInterval: 1 },
              series: data.emoticons.series.map((s, i) => ({
                name: s.name,
                type: "bar" as const,
                itemStyle: { color: seriesColor(s, i) },
                data: s.values,
              })),
            }}
            onEvents={{
              click: (p: { dataIndex: number; seriesIndex: number }) => {
                const s = data.emoticons.series[p.seriesIndex];
                drill(
                  data.emoticons.drilldowns?.[p.dataIndex] ?? threadQ,
                  s?.person_id != null ? { sender_id: s.person_id } : undefined,
                );
              },
            }}
          />
        </ChartCard>

        <ChartCard
          title="First use of top emojis"
          info="When each of the top emojis appeared for the first time in this chat. Click to see the message in context."
          data={recordsToTable(
            data.first_use.map((f) => ({
              emoji: f.emoji,
              date: f.message.ts,
              sender: f.message.sender.name,
            })),
          )}
          empty={data.first_use.length === 0}
          height={200}
        >
          <ul className="grid max-h-96 grid-cols-1 gap-1 overflow-auto sm:grid-cols-2">
            {data.first_use.map((f) => (
              <li key={f.emoji}>
                <button
                  type="button"
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-sm hover:bg-surface-2"
                  onClick={() => openContext(f.message.message_id, f.message.thread_id)}
                >
                  <span className="text-xl" aria-hidden="true">
                    {f.emoji}
                  </span>
                  <span className="sr-only">{f.emoji}</span>
                  <span className="tabular-nums text-muted">{formatDate(f.message.ts)}</span>
                  <PersonBadge
                    name={f.message.sender.name}
                    id={f.message.sender.person_id}
                    color_index={f.message.sender.color_index}
                    size="xs"
                    showName
                    className="ml-auto"
                  />
                </button>
              </li>
            ))}
          </ul>
        </ChartCard>
      </div>
      <p className="text-xs text-muted">
        {data.merge_skin_tones ? "Skin tones merged. " : ""}
        {data.merge_gendered ? "Gendered variants merged. " : ""}
        Times in {filters.tz}.
      </p>
    </div>
  );
}

function PersonEmojiCard({
  p,
  onChain,
}: {
  p: PersonEmojiStats;
  onChain: (c: EmojiChain) => void;
}) {
  const drill = useDrill();
  const sig = p.signature;
  return (
    <section className="card flex flex-col gap-3 p-4" aria-label={`Emojis of ${p.person.name}`}>
      <div className="flex items-center gap-3">
        <PersonBadge
          name={p.person.name}
          id={p.person.person_id}
          color_index={p.person.color_index}
          size="md"
          showName
        />
        {sig && (
          <button
            type="button"
            className="ml-auto flex items-center gap-2 rounded-lg border border-border px-3 py-1 hover:bg-surface-2"
            title="Signature emoji: used most distinctively compared with the others (log-odds)"
            onClick={() => drill(sig.drilldown ?? { ...p.drilldown, emoji: sig.emoji })}
          >
            <span className="text-4xl leading-none">{sig.emoji}</span>
            <span className="flex flex-col text-left text-xs text-muted">
              <span>Signature</span>
              <span className="tabular-nums">{formatNumber(sig.count)}×</span>
            </span>
          </button>
        )}
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Tile label="Emojis" value={p.total} query={toMessageQuery(p.drilldown)} />
        <Tile
          label="Per message"
          value={formatNumber(p.per_message, { maximumFractionDigits: 2 })}
          format="text"
        />
        <Tile label="Msgs with emoji" value={p.with_emoji_rate} format="percent" />
        <Tile label="Unique" value={p.unique} />
        <Tile label="Emoji-only msgs" value={p.emoji_only_messages} />
        <Tile label="Emoticons" value={p.emoticons} />
        <Tile
          label="Longest chain"
          value={p.longest_chain?.length ?? null}
          hint={p.longest_chain?.emojis.slice(0, 24)}
          onClick={p.longest_chain ? () => onChain(p.longest_chain!) : undefined}
          className="col-span-2"
        />
      </div>
    </section>
  );
}
