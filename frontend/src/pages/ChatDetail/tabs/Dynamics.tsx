import { escapeHtml } from "@/lib/utils";
import { categoryTable, timeSeriesTable } from "@/charts/tables";
import { useMemo, useState } from "react";
import { useApi } from "@/api/useApi";
import { ChartCard } from "@/charts/ChartCard";
import { recordsToTable } from "@/charts/csv";
import { EChart } from "@/charts/EChart";
import type { EChartsOption } from "@/charts/registerEcharts";
import { defaultGrid, timeSeriesZoom } from "@/charts/sampling";
import { ErrorCard } from "@/components/ErrorCard";
import { PersonBadge } from "@/components/PersonBadge";
import { Tile, TileGrid } from "@/components/Tile";
import {
  formatMs,
  mapValues,
  radarIndicators,
  replyBoxRows,
  seriesColor,
  shortSeconds,
} from "@/features/chat/charts";
import { parseChatId } from "@/features/chat/context";
import { toMessageQuery, useDrill } from "@/lib/drilldown";
import type { ApiCategoryChart, ChatDynamics, PersonRef } from "@/features/chat/types";
import { Segmented } from "@/components/Segmented";
import { InfoCard, MessageQuote } from "@/features/chat/ui";
import { personColor } from "@/lib/colors";
import { formatDateTime, formatNumber, formatPercent } from "@/lib/format";
import { useFilters } from "@/state/filters";
import type { ChatTabProps } from "@/pages/ChatDetail/tabs";

const color = (p: PersonRef) => personColor({ id: p.person_id, color_index: p.color_index });

/** Donut from a CategoryChart whose categories are person names, colored via `people`. */
function personDonut(chart: ApiCategoryChart, people: PersonRef[]): EChartsOption {
  const byName = new Map(people.map((p) => [p.name, p]));
  const s = chart.series[0];
  return {
    tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
    legend: { type: "scroll", bottom: 0 },
    series: [
      {
        type: "pie",
        radius: ["45%", "72%"],
        center: ["50%", "45%"],
        label: { formatter: "{b}\n{d}%" },
        data: chart.categories.map((c, i) => {
          const p = byName.get(c);
          return {
            name: c,
            value: s?.values[i] ?? 0,
            itemStyle: { color: p ? color(p) : personColor(c) },
          };
        }),
      },
    ],
  };
}

const secAxis = (name?: string) => ({
  type: "log" as const,
  logBase: 10,
  name,
  min: 1,
  axisLabel: { formatter: (v: number) => shortSeconds(v) },
});

type Hist = "messages" | "duration";

export default function DynamicsTab({ chatId }: ChatTabProps) {
  const id = parseChatId(chatId);
  const q = useApi("/api/chats/{chat_id}/dynamics", { path: { chat_id: id } });
  const open = useDrill();
  const { filters } = useFilters();
  const d: ChatDynamics | undefined = q.data;
  const [hist, setHist] = useState<Hist>("messages");

  const people = useMemo(() => d?.people.map((p) => p.person) ?? [], [d]);
  const opts = useMemo(() => (d ? buildOptions(d, people) : null), [d, people]);

  if (q.error && !d) return <ErrorCard error={q.error} onRetry={() => void q.refetch()} />;

  const s = d?.sessions;
  const histChart = d
    ? hist === "messages"
      ? d.session_length_histogram
      : d.session_duration_histogram
    : null;
  const catClick = (chart: ApiCategoryChart | undefined) => ({
    click: (p: { dataIndex: number }) => open(chart?.drilldowns?.[p.dataIndex]),
  });

  return (
    <div className="flex flex-col gap-4">
      <TileGrid>
        <Tile
          label="Sessions"
          loading={!d}
          value={s?.count}
          query={s ? toMessageQuery(s.drilldown) : undefined}
          hint={d ? `gap > ${formatMs(d.session_gap_ms)}` : undefined}
        />
        <Tile
          label="Avg messages / session"
          loading={!d}
          value={s?.avg_messages ?? null}
          hint={s ? `median ${formatNumber(s.median_messages)}` : undefined}
        />
        <Tile
          label="Avg session length"
          loading={!d}
          value={s?.avg_duration_ms != null ? s.avg_duration_ms / 1000 : null}
          format="duration"
          hint={s ? `median ${formatMs(s.median_duration_ms)}` : undefined}
        />
        {s?.longest && (
          <Tile
            label="Longest session"
            value={s.longest.duration_ms / 1000}
            format="duration"
            hint={`${formatDateTime(s.longest.start, filters.tz)} · ${formatNumber(s.longest.messages)} msgs`}
            query={toMessageQuery(s.longest.drilldown)}
          />
        )}
        {s?.most_messages && s.most_messages.session_id !== s.longest?.session_id && (
          <Tile
            label="Most messages in a session"
            value={s.most_messages.messages}
            hint={`${formatDateTime(s.most_messages.start, filters.tz)} · ${formatMs(s.most_messages.duration_ms)}`}
            query={toMessageQuery(s.most_messages.drilldown)}
          />
        )}
      </TileGrid>

      <div className="card-grid">
        <ChartCard
          title="Session length distribution"
          info="How many sessions had a given length. Bins grow exponentially (log scale). A session ends after a gap longer than the session gap setting."
          query={q}
          data={
            histChart
              ? categoryTable(histChart, hist === "messages" ? "Messages" : "Duration")
              : null
          }
          actions={
            <Segmented
              value={hist}
              onChange={setHist}
              label="Measure"
              options={[
                { value: "messages", label: "Messages" },
                { value: "duration", label: "Duration" },
              ]}
            />
          }
        >
          {histChart && (
            <EChart
              option={{
                tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
                grid: defaultGrid(),
                xAxis: {
                  type: "category",
                  data: histChart.categories,
                  name: hist === "messages" ? "messages" : "",
                },
                yAxis: { type: "value", name: "Sessions" },
                series: [
                  {
                    type: "bar",
                    name: "Sessions",
                    data: histChart.series[0]?.values ?? [],
                    color: personColor(4),
                  },
                ],
              }}
            />
          )}
        </ChartCard>
        <ChartCard
          title="Who starts conversations"
          info="Sessions started by each person (the sender of the first message after a gap)."
          query={q}
          data={d ? categoryTable(d.initiators, "Person") : null}
        >
          {opts && (
            <EChart option={opts.initiators} onEvents={catClick(d?.initiators)} height={300} />
          )}
        </ChartCard>
        <ChartCard
          title="Conversation starts over time"
          info="Sessions started per month, per person."
          query={q}
          data={d ? timeSeriesTable(d.initiators_over_time) : null}
        >
          {opts && <EChart option={opts.initiatorsTime} height={300} />}
        </ChartCard>
        <ChartCard
          title="Who has the last word"
          info="Sessions in which each person sent the last message."
          query={q}
          data={d ? categoryTable(d.last_word, "Person") : null}
        >
          {opts && <EChart option={opts.lastWord} onEvents={catClick(d?.last_word)} height={300} />}
        </ChartCard>
      </div>

      <TileGrid>
        {d?.replies
          .flatMap((r) => [
            <Tile
              key={`rr${r.person.person_id}`}
              label={
                <>
                  <PersonBadge
                    name={r.person.name}
                    id={r.person.person_id}
                    color_index={r.person.color_index}
                    size="xs"
                  />{" "}
                  Reply rate
                </>
              }
              value={r.reply_rate ?? null}
              format="percent"
              hint={`${r.person.name}'s turns answered · ${formatNumber(r.replied_turns)}/${formatNumber(r.turns)}`}
            />,
            <Tile
              key={`lr${r.person.person_id}`}
              label={
                <>
                  <PersonBadge
                    name={r.person.name}
                    id={r.person.person_id}
                    color_index={r.person.color_index}
                    size="xs"
                  />{" "}
                  Left on read
                </>
              }
              value={r.unanswered_turns}
              hint={`${r.person.name}'s turns with no reply within ${formatMs(d.reply_window_ms)}`}
              query={toMessageQuery(r.drilldown)}
            />,
            <Tile
              key={`md${r.person.person_id}`}
              label={
                <>
                  <PersonBadge
                    name={r.person.name}
                    id={r.person.person_id}
                    color_index={r.person.color_index}
                    size="xs"
                  />{" "}
                  Median reply
                </>
              }
              value={r.median_ms != null ? r.median_ms / 1000 : null}
              format="duration"
              hint={`${r.person.name} · fastest ${formatMs(r.fastest_ms)}`}
            />,
          ])
          .slice(0, 18)}
      </TileGrid>

      <div className="card-grid">
        <ChartCard
          title="Reply latency"
          info={`Time to reply per person (log scale). Box = 25th–75th percentile with the median; whiskers go from the fastest reply to the 90th percentile. Only replies within the reply window (${d ? formatMs(d.reply_window_ms) : "12 h"}) count.`}
          query={q}
          empty={q.isSuccess && !d?.replies.some((r) => r.replies > 0)}
          data={
            d
              ? recordsToTable(
                  d.replies.map((r) => ({
                    person: r.person.name,
                    replies: r.replies,
                    fastest_s: r.fastest_ms == null ? null : r.fastest_ms / 1000,
                    p25_s: r.p25_ms == null ? null : r.p25_ms / 1000,
                    median_s: r.median_ms == null ? null : r.median_ms / 1000,
                    mean_s: r.mean_ms == null ? null : r.mean_ms / 1000,
                    p75_s: r.p75_ms == null ? null : r.p75_ms / 1000,
                    p90_s: r.p90_ms == null ? null : r.p90_ms / 1000,
                    slowest_s: r.slowest_ms == null ? null : r.slowest_ms / 1000,
                  })),
                )
              : null
          }
        >
          {opts && <EChart option={opts.box} height={300} />}
        </ChartCard>
        <ChartCard
          title="Reply latency distribution"
          info="Number of replies per latency bin (log bins from 1 s up to the reply window), per person."
          query={q}
          data={d ? categoryTable(d.reply_histogram, "Latency ≤") : null}
        >
          {opts && <EChart option={opts.replyHist} height={300} />}
        </ChartCard>
        <ChartCard
          title="Median reply time per month"
          info="Each person's median reply time per month (log scale)."
          query={q}
          data={
            d
              ? timeSeriesTable({
                  ...d.reply_median_over_time,
                  series: d.reply_median_over_time.series.map((x) => ({
                    ...x,
                    name: `${x.name} (ms)`,
                  })),
                })
              : null
          }
        >
          {opts && <EChart option={opts.replyTime} height={300} />}
        </ChartCard>
        <ChartCard
          title="Reply time by hour of day"
          info="Median reply time by the hour the reply was sent — are replies faster in the evening?"
          query={q}
          data={
            d
              ? categoryTable(
                  {
                    ...d.reply_median_by_hour,
                    series: d.reply_median_by_hour.series.map((x) => ({
                      ...x,
                      name: `${x.name} (ms)`,
                    })),
                  },
                  "Hour",
                )
              : null
          }
        >
          {opts && <EChart option={opts.replyHour} height={300} />}
        </ChartCard>
        <ChartCard
          title="Turns and double texts"
          info={`A turn is a run of consecutive messages from one person. Left: average messages per turn. Right: double (2 bursts) and triple (3+) texts — new burst after > 1 minute without a message.`}
          query={q}
          data={d ? categoryTable(d.turns_chart, "Person") : null}
        >
          {opts && <EChart option={opts.turns} onEvents={catClick(d?.turns_chart)} height={300} />}
        </ChartCard>
        <ChartCard
          title="Effort index"
          info="Each part as a z-score across the people in this chat (0 = average; reply speed is inverted so faster = higher). Combined z is the average of the parts — always read it with its parts."
          query={q}
          empty={q.isSuccess && !d?.effort.length}
          data={
            d
              ? recordsToTable(
                  d.effort.flatMap((e) =>
                    e.parts.map((p) => ({
                      person: e.person.name,
                      part: p.label,
                      value: p.value,
                      unit: p.unit,
                      z: p.z,
                    })),
                  ),
                )
              : null
          }
        >
          {opts && d && (
            <>
              <EChart option={opts.radar} height={300} />
              <EffortTable d={d} />
            </>
          )}
        </ChartCard>
      </div>

      <div className="card-grid">
        <InfoCard title="Longest wait that got a reply">
          {d?.longest_wait ? (
            <>
              <p className="text-sm">
                <PersonBadge
                  name={d.longest_wait.replier.name}
                  id={d.longest_wait.replier.person_id}
                  color_index={d.longest_wait.replier.color_index}
                  size="xs"
                  showName
                  className="font-medium"
                />{" "}
                replied after <strong>{formatMs(d.longest_wait.latency_ms)}</strong>
                {d.longest_wait.waited_for && <> to {d.longest_wait.waited_for.name}</>}.
              </p>
              <MessageQuote
                message={d.longest_wait.before}
                drilldown={d.longest_wait.drilldown}
                label="Before"
              />
              <MessageQuote
                message={d.longest_wait.reply}
                drilldown={d.longest_wait.drilldown}
                label="Reply"
              />
            </>
          ) : (
            <p className="text-sm text-muted">No replies in scope.</p>
          )}
        </InfoCard>
        <InfoCard title="Longest monologue">
          {d?.longest_turn ? (
            <>
              <p className="text-sm">
                <PersonBadge
                  name={d.longest_turn.person.name}
                  id={d.longest_turn.person.person_id}
                  color_index={d.longest_turn.person.color_index}
                  size="xs"
                  showName
                  className="font-medium"
                />{" "}
                sent{" "}
                <button
                  type="button"
                  className="font-semibold hover:text-accent hover:underline"
                  onClick={() => open(d.longest_turn!.drilldown)}
                >
                  {formatNumber(d.longest_turn.messages)} messages
                </button>{" "}
                in a row over {formatMs(d.longest_turn.duration_ms)}.
              </p>
              <MessageQuote
                message={d.longest_turn.first_message}
                drilldown={d.longest_turn.drilldown}
                label="It started with"
              />
            </>
          ) : (
            <p className="text-sm text-muted">No turns in scope.</p>
          )}
        </InfoCard>
        <InfoCard title="Conversation starters">
          {d?.starters.length ? (
            <ol className="flex flex-col divide-y divide-border text-sm">
              {d.starters.map((st, i) => (
                <li key={i}>
                  <button
                    type="button"
                    className="flex w-full items-center gap-3 px-1 py-1.5 text-left hover:bg-surface-2"
                    onClick={() => open(st.drilldown)}
                  >
                    <span className="min-w-0 flex-1 truncate">
                      {st.text ?? <span className="italic text-muted">hidden</span>}
                    </span>
                    <span className="tabular-nums text-muted">{formatNumber(st.count)}×</span>
                  </button>
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-sm text-muted">No repeated session openers.</p>
          )}
        </InfoCard>
      </div>

      {d?.notes.length ? (
        <ul className="list-disc pl-5 text-xs text-muted">
          {d.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function EffortTable({ d }: { d: ChatDynamics }) {
  const parts = d.effort[0]?.parts ?? [];
  const fmt = (v: number | null | undefined, unit: string | null | undefined) =>
    unit === "ratio"
      ? formatPercent(v)
      : unit === "ms"
        ? formatMs(v)
        : formatNumber(v, { maximumFractionDigits: 2 });
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <caption className="sr-only">Effort index parts</caption>
        <thead>
          <tr className="text-muted">
            <th scope="col" className="py-1 pr-2 font-medium">
              Person
            </th>
            {parts.map((p) => (
              <th key={p.key} scope="col" className="py-1 pr-2 font-medium">
                {p.label}
              </th>
            ))}
            <th scope="col" className="py-1 font-medium">
              Combined z
            </th>
          </tr>
        </thead>
        <tbody>
          {d.effort.map((e) => (
            <tr key={e.person.person_id} className="border-t border-border">
              <td className="py-1 pr-2">
                <PersonBadge
                  name={e.person.name}
                  id={e.person.person_id}
                  color_index={e.person.color_index}
                  size="xs"
                  showName
                />
              </td>
              {e.parts.map((p) => (
                <td key={p.key} className="py-1 pr-2 tabular-nums">
                  {fmt(p.value, p.unit)}{" "}
                  <span className="text-muted">
                    (
                    {p.z == null
                      ? "—"
                      : `${p.z >= 0 ? "+" : ""}${formatNumber(p.z, { maximumFractionDigits: 2 })}`}
                    )
                  </span>
                </td>
              ))}
              <td className="py-1 tabular-nums">
                {formatNumber(e.combined_z, { maximumFractionDigits: 2 })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function buildOptions(d: ChatDynamics, people: PersonRef[]) {
  const legend = { type: "scroll" as const, top: 0 };
  const sec = (v: number | null) => (v == null ? null : Math.max(v / 1000, 1));

  const initiators = personDonut(d.initiators, people);
  const lastWord = personDonut(d.last_word, people);

  const initiatorsTime: EChartsOption = {
    legend,
    tooltip: { trigger: "axis" },
    grid: defaultGrid({ slider: true, legend: true }),
    dataZoom: timeSeriesZoom(),
    xAxis: { type: "time" },
    yAxis: { type: "value", name: "Sessions" },
    series: d.initiators_over_time.series.map((s) => ({
      type: "line",
      name: s.name,
      showSymbol: false,
      color: seriesColor(s),
      data: d.initiators_over_time.x.map((x, i) => [x, s.values[i] ?? null]),
    })),
  };

  const boxRows = replyBoxRows(d.replies);
  const box: EChartsOption = {
    tooltip: {
      trigger: "item",
      formatter: (p: unknown) => {
        const { dataIndex } = p as { dataIndex: number };
        const r = d.replies[dataIndex];
        if (!r) return "";
        return [
          `<b>${escapeHtml(r.person.name)}</b> (${formatNumber(r.replies)} replies)`,
          `fastest ${formatMs(r.fastest_ms)}`,
          `p25 ${formatMs(r.p25_ms)}`,
          `median ${formatMs(r.median_ms)}`,
          `p75 ${formatMs(r.p75_ms)}`,
          `p90 ${formatMs(r.p90_ms)}`,
          `mean ${formatMs(r.mean_ms)}`,
        ].join("<br/>");
      },
    },
    grid: defaultGrid(),
    xAxis: { type: "category", data: d.replies.map((r) => r.person.name) },
    yAxis: { ...secAxis(), max: d.reply_window_ms / 1000 },
    series: [
      {
        type: "boxplot",
        data: boxRows.map((row, i) => ({
          value: row ?? [],
          itemStyle: {
            color: "transparent",
            borderColor: color(d.replies[i]!.person),
            borderWidth: 2,
          },
        })),
      },
    ],
  };

  const edges = d.reply_histogram_edges_ms;
  const replyHist: EChartsOption = {
    legend,
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: defaultGrid({ legend: true }),
    xAxis: {
      type: "category",
      data: d.reply_histogram.categories,
      name: edges.length ? `≤ ${shortSeconds(edges[edges.length - 1]! / 1000)}` : undefined,
    },
    yAxis: { type: "value", name: "Replies" },
    series: d.reply_histogram.series.map((s) => ({
      type: "bar",
      name: s.name,
      color: seriesColor(s),
      data: s.values,
    })),
  };

  const replyTime: EChartsOption = {
    legend,
    tooltip: { trigger: "axis", valueFormatter: (v) => formatMs((v as number) * 1000) },
    grid: defaultGrid({ slider: true, legend: true }),
    dataZoom: timeSeriesZoom(),
    xAxis: { type: "time" },
    yAxis: secAxis(),
    series: d.reply_median_over_time.series.map((s) => ({
      type: "line",
      name: s.name,
      showSymbol: false,
      connectNulls: true,
      color: seriesColor(s),
      data: d.reply_median_over_time.x.map((x, i) => [x, sec(s.values[i] ?? null)]),
    })),
  };

  const replyHour: EChartsOption = {
    legend,
    tooltip: { trigger: "axis", valueFormatter: (v) => formatMs((v as number) * 1000) },
    grid: defaultGrid({ legend: true }),
    xAxis: {
      type: "category",
      data: d.reply_median_by_hour.categories.map((c) => c.padStart(2, "0")),
    },
    yAxis: secAxis(),
    series: d.reply_median_by_hour.series.map((s) => ({
      type: "line",
      name: s.name,
      connectNulls: true,
      color: seriesColor(s),
      data: mapValues(s.values, (v) => Math.max(v / 1000, 1)),
    })),
  };

  // Turns: left grid avg/turn, right grid double/triple.
  const tc = d.turns_chart;
  const avg = tc.series.find((s) => s.key === "avg_messages_per_turn");
  const dbl = tc.series.find((s) => s.key === "double_texts");
  const tpl = tc.series.find((s) => s.key === "triple_texts");
  const personColors = tc.categories.map((name) => {
    const p = people.find((x) => x.name === name);
    return p ? color(p) : personColor(name);
  });
  const turns: EChartsOption = {
    legend: { top: 0, data: [dbl?.name ?? "Double texts", tpl?.name ?? "Triple texts"] },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    title: [
      {
        text: avg?.name ?? "Messages per turn",
        left: "2%",
        top: 20,
        textStyle: { fontSize: 12, fontWeight: "normal" },
      },
    ],
    grid: [
      { left: "2%", width: "38%", top: 48, bottom: 8, containLabel: true },
      { left: "48%", right: "2%", top: 48, bottom: 8, containLabel: true },
    ],
    xAxis: [
      { type: "category", gridIndex: 0, data: tc.categories },
      { type: "category", gridIndex: 1, data: tc.categories },
    ],
    yAxis: [
      { type: "value", gridIndex: 0 },
      { type: "value", gridIndex: 1 },
    ],
    series: [
      {
        type: "bar",
        name: avg?.name ?? "Messages per turn",
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: (avg?.values ?? []).map((v, i) => ({
          value: v == null ? null : Math.round(v * 100) / 100,
          itemStyle: { color: personColors[i] },
        })),
      },
      {
        type: "bar",
        name: dbl?.name ?? "Double texts",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: dbl?.values ?? [],
        color: personColor(4),
      },
      {
        type: "bar",
        name: tpl?.name ?? "Triple texts",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: tpl?.values ?? [],
        color: personColor(5),
      },
    ],
  };

  const parts = d.effort[0]?.parts ?? [];
  const zs = d.effort.map((e) => parts.map((p) => e.parts.find((x) => x.key === p.key)?.z ?? null));
  const radar: EChartsOption = {
    legend: { type: "scroll", bottom: 0 },
    tooltip: { trigger: "item" },
    radar: {
      indicator: radarIndicators(parts, zs),
      radius: "62%",
      center: ["50%", "48%"],
      axisName: { fontSize: 11 },
    },
    series: [
      {
        type: "radar",
        data: d.effort.map((e, i) => ({
          name: e.person.name,
          value: zs[i]!.map((z) => (z == null ? 0 : Math.round(z * 100) / 100)),
          itemStyle: { color: color(e.person) },
          lineStyle: { color: color(e.person) },
          areaStyle: { opacity: 0.15 },
        })),
      },
    ],
  };

  return {
    initiators,
    lastWord,
    initiatorsTime,
    box,
    replyHist,
    replyTime,
    replyHour,
    turns,
    radar,
  };
}
