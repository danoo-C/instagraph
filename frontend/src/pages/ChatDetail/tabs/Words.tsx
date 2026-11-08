import { categoryTable } from "@/charts/tables";
import { useMemo, useState } from "react";
import { useApi } from "@/api/useApi";
import type { components } from "@/api/schema";
import { ChartCard } from "@/charts/ChartCard";
import { recordsToTable } from "@/charts/csv";
import { EChart } from "@/charts/EChart";
import type { EChartsOption } from "@/charts/registerEcharts";
import { defaultGrid } from "@/charts/sampling";
import { DataTable } from "@/components/DataTable";
import { dataTableColumnHelper } from "@/components/dataTableColumns";
import { PersonBadge } from "@/components/PersonBadge";
import { Tile, TileGrid } from "@/components/Tile";
import { categoryToEcharts, isEmptyChart, personRefColor } from "@/features/tabs/charts";
import { toMessageQuery, useDrill, useOpenContext } from "@/lib/drilldown";
import { Segmented } from "@/components/Segmented";
import { SelectInput } from "@/components/SelectInput";
import { SectionTitle, TabState } from "@/features/tabs/ui";
import { divergingRows, laughPairRows } from "@/features/tabs/words";
import { TermTracker } from "@/features/tabs/wordsTermTracker";
import { formatDate, formatNumber, formatPercent } from "@/lib/format";
import { DIVERGING, OTHERS_COLOR } from "@/lib/colors";
import type { ChatTabProps } from "@/pages/ChatDetail/tabs";

type S = components["schemas"];
type WordsResponse = S["WordsResponse"];
type TermCount = S["TermCount"];
type TermLists = S["TermLists"];
type RepeatedMessage = S["RepeatedMessage"];

const HIDDEN = "Hidden in anonymize mode";

export default function WordsTab({ chatId }: ChatTabProps) {
  const id = Number(chatId);
  const [accentInsensitive, setAccentInsensitive] = useState(false);
  const q = useApi("/api/chats/{chat_id}/words", {
    path: { chat_id: id },
    query: { accent_insensitive: accentInsensitive },
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <label className="flex items-center gap-1.5 text-sm">
          <input
            type="checkbox"
            checked={accentInsensitive}
            onChange={(e) => setAccentInsensitive(e.target.checked)}
          />
          Merge words that differ only in accents
        </label>
      </div>
      <TabState query={q}>{() => <WordsBody data={q.data!} chatId={id} />}</TabState>
    </div>
  );
}

function WordsBody({ data, chatId }: { data: WordsResponse; chatId: number }) {
  const drill = useDrill();
  const openContext = useOpenContext();
  const hidden = data.text_hidden;

  return (
    <>
      <SectionTitle>Vocabulary</SectionTitle>
      <TileGrid>
        {data.people.flatMap((p) => [
          <Tile
            key={`v${p.person.person_id}`}
            label={
              <>
                <PersonBadge {...personProps(p.person)} size="xs" /> {p.person.name} · vocabulary
              </>
            }
            value={p.vocabulary_size}
            hint={`${formatNumber(p.words)} words · ${formatNumber(p.avg_words_per_message, { maximumFractionDigits: 1 })} per message`}
            query={toMessageQuery(p.drilldown)}
          />,
          <Tile
            key={`m${p.person.person_id}`}
            label={`${p.person.name} · lexical diversity (MATTR)`}
            value={p.lexical_diversity}
            format="percent"
            hint="Moving-average type-token ratio, window 500"
            query={toMessageQuery(p.drilldown)}
          />,
        ])}
      </TileGrid>

      <TermTracker chatId={chatId} savedTerms={data.saved_terms} />

      <div className="card-grid">
        <TopWordsCard data={data} />
        <DistinctiveCard data={data} />
        <NgramCard title="Top bigrams" lists={data.bigrams} hidden={hidden} />
        <NgramCard title="Top trigrams" lists={data.trigrams} hidden={hidden} />
        <LengthCard data={data} />
        <RatesCard data={data} />
      </div>

      <SectionTitle>Laughter</SectionTitle>
      <TileGrid>
        {data.people.map((p) => (
          <Tile
            key={p.person.person_id}
            label={`${p.person.name} · laughs`}
            value={p.laugh_messages}
            hint={`${formatNumber(p.laughs_per_100, { maximumFractionDigits: 1 })} per 100 messages · caused ${formatNumber(p.laughs_caused)}`}
            query={toMessageQuery(p.drilldown)}
          />
        ))}
      </TileGrid>
      <div className="card-grid">
        <LaughterCard data={data} />
        <LaughPairsCard data={data} />
      </div>

      <div className="card-grid">
        <ChartCard
          title="Most repeated messages"
          info="Messages sent with exactly the same text, most frequent first. Click a row to open them."
          empty={hidden || data.repeated_messages.length === 0}
          emptyMessage={hidden ? HIDDEN : undefined}
          data={recordsToTable(
            data.repeated_messages.map((r) => ({ text: r.text ?? "", count: r.count })),
            [
              { key: "text", header: "Message" },
              { key: "count", header: "Times" },
            ],
          )}
        >
          <RepeatedTable rows={data.repeated_messages} onOpen={(r) => drill(r.drilldown)} />
        </ChartCard>
        <ChartCard
          title="Longest messages"
          info="Top messages by length per person. Click one to see it in context."
          empty={data.longest_messages.every((p) => p.messages.length === 0)}
          data={recordsToTable(
            data.longest_messages.flatMap((p) =>
              p.messages.map((m) => ({
                person: p.person.name,
                date: m.ts,
                text: m.text ?? "",
              })),
            ),
          )}
        >
          <div className="flex max-h-96 flex-col gap-3 overflow-auto">
            {data.longest_messages.map((p) => (
              <div key={p.person.person_id} className="flex flex-col gap-1">
                <PersonBadge {...personProps(p.person)} showName size="xs" />
                <ol className="flex flex-col gap-1">
                  {p.messages.map((m) => (
                    <li key={m.message_id}>
                      <button
                        type="button"
                        onClick={() => openContext(m.message_id, m.thread_id)}
                        className="w-full rounded-md border border-border px-2 py-1 text-left text-sm hover:border-accent/60 hover:bg-surface-2"
                      >
                        <span className="block text-xs text-muted">{formatDate(m.ts)}</span>
                        <span className="line-clamp-3 whitespace-pre-wrap break-words">
                          {m.text ?? "(text hidden)"}
                        </span>
                      </button>
                    </li>
                  ))}
                </ol>
              </div>
            ))}
          </div>
        </ChartCard>
        <MentionsCard data={data} />
        {data.swear && <SwearCard data={data} />}
      </div>
    </>
  );
}

// ---------------------------------------------------------------- helpers

function personProps(p: S["PersonRef"]) {
  return { name: p.name, id: p.person_id, color_index: p.color_index };
}

function PersonSelect({
  people,
  value,
  onChange,
  allowOverall,
}: {
  people: S["PersonRef"][];
  value: string;
  onChange: (v: string) => void;
  allowOverall?: boolean;
}) {
  return (
    <SelectInput label="Person" value={value} onChange={onChange} className="max-w-40">
      {allowOverall && <option value="all">Everyone</option>}
      {people.map((p) => (
        <option key={p.person_id} value={String(p.person_id)}>
          {p.name}
        </option>
      ))}
    </SelectInput>
  );
}

function pickTerms(lists: TermLists, who: string): TermCount[] {
  if (who === "all") return lists.overall;
  return lists.by_person.find((p) => String(p.person.person_id) === who)?.terms ?? [];
}

// ---------------------------------------------------------------- cards

function TopWordsCard({ data }: { data: WordsResponse }) {
  const drill = useDrill();
  const [who, setWho] = useState("all");
  const [view, setView] = useState<"bar" | "cloud">("bar");
  const people = data.top_words.by_person.map((p) => p.person);
  const terms = pickTerms(data.top_words, who).slice(0, 50);
  const person = people.find((p) => String(p.person_id) === who);
  const color = person ? personRefColor(person) : "#4f46e5";

  const option = useMemo((): EChartsOption => {
    if (view === "cloud")
      return {
        tooltip: {},
        series: [
          {
            type: "wordCloud",
            shape: "circle",
            sizeRange: [12, 52],
            rotationRange: [0, 0],
            gridSize: 6,
            width: "100%",
            height: "100%",
            textStyle: { color },
            emphasis: { textStyle: { fontWeight: "bold" } },
            data: terms.map((t) => ({ name: t.term, value: t.count })),
          },
        ],
      } as unknown as EChartsOption;
    const rows = [...terms].reverse();
    return {
      grid: defaultGrid(),
      tooltip: { trigger: "axis" as const, axisPointer: { type: "shadow" as const } },
      xAxis: { type: "value" as const },
      yAxis: {
        type: "category" as const,
        data: rows.map((t) => t.term),
        axisLabel: { interval: 0 },
      },
      series: [
        { type: "bar", name: "Count", itemStyle: { color }, data: rows.map((t) => t.count) },
      ],
    };
  }, [terms, view, color]);

  return (
    <ChartCard
      title="Top words"
      info="Most used words (stop-words removed). Toggle between bars and a word cloud; click a word to open the messages."
      empty={data.text_hidden || terms.length === 0}
      emptyMessage={data.text_hidden ? HIDDEN : undefined}
      data={recordsToTable(terms, [
        { key: "term", header: "Word" },
        { key: "count", header: "Count" },
      ])}
      height={view === "bar" ? Math.max(320, terms.length * 18) : 360}
      actions={
        <>
          <PersonSelect people={people} value={who} onChange={setWho} allowOverall />
          <Segmented
            label="View"
            value={view}
            onChange={setView}
            options={[
              { value: "bar", label: "Bars" },
              { value: "cloud", label: "Cloud" },
            ]}
          />
        </>
      }
    >
      <EChart
        option={option}
        height={view === "bar" ? Math.max(320, terms.length * 18) : 360}
        onEvents={{
          click: (p: { name: string }) => drill(terms.find((t) => t.term === p.name)?.drilldown),
        }}
      />
    </ChartCard>
  );
}

function DistinctiveCard({ data }: { data: WordsResponse }) {
  const drill = useDrill();
  const byPerson = data.distinctive.by_person;
  const people = byPerson.map((p) => p.person);
  const [who, setWho] = useState<string>(people[0] ? String(people[0].person_id) : "");
  const twoPeople = people.length === 2;
  const right = twoPeople ? people[1]! : people.find((p) => String(p.person_id) === who);
  const left = twoPeople ? people[0]! : null;
  const rows = right ? divergingRows(byPerson, right.person_id, left?.person_id) : [];
  const colorOf = (pid: number) => {
    const p = people.find((x) => x.person_id === pid);
    return p ? personRefColor(p) : OTHERS_COLOR;
  };

  const option: EChartsOption = {
    grid: defaultGrid(),
    tooltip: {
      trigger: "axis" as const,
      axisPointer: { type: "shadow" as const },
      valueFormatter: (v: unknown) =>
        formatNumber(Math.abs(Number(v)), { maximumFractionDigits: 2 }),
    },
    xAxis: {
      type: "value" as const,
      axisLabel: { formatter: (v: number) => formatNumber(Math.abs(v)) },
    },
    yAxis: { type: "category" as const, data: rows.map((r) => r.term), axisLabel: { interval: 0 } },
    series: [
      {
        type: "bar",
        name: data.distinctive.method === "log_odds" ? "Log-odds z-score" : "TF-IDF",
        data: rows.map((r) => ({
          value: r.value,
          itemStyle: { color: r.personId != null ? colorOf(r.personId) : DIVERGING[0] },
        })),
      },
    ],
  };
  const height = Math.max(320, rows.length * 16);
  const termQuery = (term: string, pid: number) =>
    pickTerms({ overall: [], by_person: byPerson }, String(pid)).find((t) => t.term === term)
      ?.drilldown;

  return (
    <ChartCard
      title="Distinctive words"
      info={
        data.distinctive.method === "log_odds"
          ? twoPeople
            ? `Words each person uses much more than the other (weighted log-odds with an informative Dirichlet prior). Left: ${left?.name}, right: ${right?.name}.`
            : "Words this person uses much more than everyone else (weighted log-odds with an informative Dirichlet prior)."
          : "Fallback: TF-IDF scores across people (fewer than 2 people wrote)."
      }
      empty={data.text_hidden || rows.length === 0}
      emptyMessage={data.text_hidden ? HIDDEN : undefined}
      data={recordsToTable(
        rows.map((r) => ({
          person: people.find((p) => p.person_id === r.personId)?.name ?? "",
          term: r.term,
          score: Math.abs(r.value),
          count: r.count,
        })),
      )}
      height={height}
      actions={
        !twoPeople && people.length > 1 ? (
          <PersonSelect people={people} value={who} onChange={setWho} />
        ) : undefined
      }
    >
      <EChart
        option={option}
        height={height}
        onEvents={{
          click: (p: { dataIndex: number }) => {
            const r = rows[p.dataIndex];
            if (r) drill(termQuery(r.term, r.personId));
          },
        }}
      />
    </ChartCard>
  );
}

function NgramCard({ title, lists, hidden }: { title: string; lists: TermLists; hidden: boolean }) {
  const drill = useDrill();
  const [who, setWho] = useState("all");
  const people = lists.by_person.map((p) => p.person);
  const rows = pickTerms(lists, who);
  const col = dataTableColumnHelper<TermCount>();
  const columns = useMemo(
    () =>
      col.columns([
        col.accessor("term", { header: "Phrase" }),
        col.accessor("count", { header: "Count", cell: (c) => formatNumber(c.getValue()) }),
      ]),
    [col],
  );
  return (
    <ChartCard
      title={title}
      info="Most frequent word sequences. Click a row to open the messages."
      empty={hidden || rows.length === 0}
      emptyMessage={hidden ? HIDDEN : undefined}
      data={recordsToTable(rows, [
        { key: "term", header: "Phrase" },
        { key: "count", header: "Count" },
      ])}
      actions={<PersonSelect people={people} value={who} onChange={setWho} allowOverall />}
    >
      <DataTable
        data={rows}
        columns={columns}
        getRowId={(r) => r.term}
        onRowClick={(r) => drill(r.drilldown)}
        caption={title}
        className="max-h-96"
      />
    </ChartCard>
  );
}

function RepeatedTable({
  rows,
  onOpen,
}: {
  rows: RepeatedMessage[];
  onOpen: (r: RepeatedMessage) => void;
}) {
  const col = dataTableColumnHelper<RepeatedMessage>();
  const columns = useMemo(
    () =>
      col.columns([
        col.accessor((r) => r.text ?? "", {
          id: "text",
          header: "Message",
          cell: (c) => <span className="line-clamp-2 whitespace-pre-wrap">{c.getValue()}</span>,
        }),
        col.accessor("count", { header: "Times", cell: (c) => formatNumber(c.getValue()) }),
        col.display({
          id: "by",
          header: "By",
          cell: (c) => (
            <span className="flex flex-wrap gap-2">
              {c.row.original.by_person
                .filter((pc) => pc.count > 0)
                .map((pc) => (
                  <span key={pc.person.person_id} className="inline-flex items-center gap-1">
                    <PersonBadge {...personProps(pc.person)} size="xs" />
                    {formatNumber(pc.count)}
                  </span>
                ))}
            </span>
          ),
        }),
      ]),
    [col],
  );
  return (
    <DataTable
      data={rows}
      columns={columns}
      getRowId={(r, i) => `${i}:${r.text ?? ""}`}
      onRowClick={onOpen}
      caption="Most repeated messages"
      className="max-h-96"
    />
  );
}

function LengthCard({ data }: { data: WordsResponse }) {
  const drill = useDrill();
  const chart = data.length_distribution;
  const option = {
    grid: defaultGrid({ legend: true }),
    legend: { top: 0 },
    tooltip: { trigger: "axis" as const, axisPointer: { type: "shadow" as const } },
    xAxis: { type: "category" as const, data: chart.categories, name: "words" },
    yAxis: { type: "value" as const },
    series: categoryToEcharts(chart).map((s) => ({
      ...s,
      barGap: "-100%",
      itemStyle: { ...s.itemStyle, opacity: 0.6 },
    })),
  };
  return (
    <ChartCard
      title="Message length distribution"
      info="Number of text messages by length in words, overlaid per person."
      empty={isEmptyChart(chart)}
      data={categoryTable(chart, "words")}
    >
      <EChart
        option={option}
        onEvents={{
          click: (p: { dataIndex: number; seriesIndex: number }) => {
            const dd = chart.drilldowns?.[p.dataIndex];
            const s = chart.series[p.seriesIndex];
            if (dd) drill(dd, s?.person_id != null ? { sender_id: s.person_id } : undefined);
          },
        }}
      />
    </ChartCard>
  );
}

const RATE_LABELS: Record<string, string> = {
  question: "Questions (?)",
  exclamation: "Exclamations (!)",
  caps: "ALL CAPS",
  ellipsis: "Ellipsis (…)",
};

function RatesCard({ data }: { data: WordsResponse }) {
  const drill = useDrill();
  const chart = data.rates;
  const option = {
    grid: defaultGrid({ legend: true }),
    legend: { top: 0 },
    tooltip: {
      trigger: "axis" as const,
      axisPointer: { type: "shadow" as const },
      valueFormatter: (v: unknown) => formatPercent(Number(v)),
    },
    xAxis: {
      type: "category" as const,
      data: chart.categories.map((c) => RATE_LABELS[c] ?? c),
    },
    yAxis: { type: "value" as const, axisLabel: { formatter: (v: number) => formatPercent(v, 0) } },
    series: categoryToEcharts(chart),
  };
  return (
    <ChartCard
      title="Question, exclamation, caps and ellipsis rates"
      info="Share of each person's text messages that contain ?, !, ALL CAPS words (≥3 letters) or an ellipsis."
      empty={isEmptyChart(chart)}
      data={categoryTable(chart, "rate")}
    >
      <EChart
        option={option}
        onEvents={{
          click: (p: { dataIndex: number; seriesIndex: number }) => {
            const s = chart.series[p.seriesIndex];
            const dd = chart.drilldowns?.[p.dataIndex];
            const person = data.people.find((x) => x.person.person_id === s?.person_id);
            drill(
              dd ?? person?.drilldown,
              s?.person_id != null ? { sender_id: s.person_id } : undefined,
            );
          },
        }}
      />
    </ChartCard>
  );
}

function LaughterCard({ data }: { data: WordsResponse }) {
  const drill = useDrill();
  const chart = data.laughter.chart;
  const option = {
    grid: defaultGrid({ legend: true }),
    legend: { top: 0 },
    tooltip: { trigger: "axis" as const, axisPointer: { type: "shadow" as const } },
    xAxis: { type: "category" as const, data: chart.categories },
    yAxis: { type: "value" as const, minInterval: 1 },
    series: categoryToEcharts(chart, {
      color: (_s, i) => (i === 0 ? "#E69F00" : "#56B4E9"),
    }),
  };
  return (
    <ChartCard
      title="Laughter sent and caused"
      info={
        <>
          Messages matching laughter patterns ({data.laughter.patterns.join(", ")}), and laughs
          credited to the person whose message came within 2 minutes before.
          {data.laughter.invalid_patterns.length > 0 &&
            ` Skipped invalid patterns: ${data.laughter.invalid_patterns.join(", ")}.`}
        </>
      }
      empty={isEmptyChart(chart)}
      data={categoryTable(chart, "person")}
    >
      <EChart
        option={option}
        onEvents={{
          click: (p: { dataIndex: number }) => {
            const dd = chart.drilldowns?.[p.dataIndex] ?? data.people[p.dataIndex]?.drilldown;
            drill(dd);
          },
        }}
      />
    </ChartCard>
  );
}

function LaughPairsCard({ data }: { data: WordsResponse }) {
  const drill = useDrill();
  const rows = laughPairRows(data.laughter.pairs).reverse();
  const option: EChartsOption = {
    grid: defaultGrid(),
    tooltip: { trigger: "axis" as const, axisPointer: { type: "shadow" as const } },
    xAxis: { type: "value" as const, minInterval: 1 },
    yAxis: {
      type: "category" as const,
      data: rows.map((r) => r.label),
      axisLabel: { interval: 0 },
    },
    series: [
      {
        type: "bar",
        name: "Laughs",
        data: rows.map((r) => ({
          value: r.count,
          itemStyle: { color: personRefColor(r.pair.causer) },
        })),
      },
    ],
  };
  return (
    <ChartCard
      title="Who makes whom laugh"
      info="“A → B”: B laughed within 2 minutes after a message from A. Colored by A. Click to open B's laughs."
      empty={rows.length === 0}
      data={recordsToTable(
        rows.map((r) => ({
          causer: r.pair.causer.name,
          laugher: r.pair.laugher.name,
          count: r.count,
        })),
      )}
    >
      <EChart
        option={option}
        height={Math.max(240, rows.length * 28)}
        onEvents={{
          click: (p: { dataIndex: number }) => {
            const r = rows[p.dataIndex];
            const person = data.people.find(
              (x) => x.person.person_id === r?.pair.laugher.person_id,
            );
            if (person) drill(person.drilldown);
          },
        }}
      />
    </ChartCard>
  );
}

function TermList({ items, onOpen }: { items: TermCount[]; onOpen: (t: TermCount) => void }) {
  return (
    <ul className="flex flex-col gap-1 text-sm">
      {items.map((t) => (
        <li key={t.term}>
          <button
            type="button"
            className="flex w-full justify-between gap-2 rounded px-1 hover:bg-surface-2"
            onClick={() => onOpen(t)}
          >
            <span className="truncate">{t.term}</span>
            <span className="tabular-nums text-muted">{formatNumber(t.count)}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function MentionsCard({ data }: { data: WordsResponse }) {
  const drill = useDrill();
  const empty = data.top_mentions.length === 0 && data.top_hashtags.length === 0;
  return (
    <ChartCard
      title="Mentions and hashtags"
      info="Most used @mentions and #hashtags in message text."
      empty={data.text_hidden || empty}
      emptyMessage={data.text_hidden ? HIDDEN : undefined}
      data={recordsToTable([
        ...data.top_mentions.map((t) => ({ kind: "mention", term: t.term, count: t.count })),
        ...data.top_hashtags.map((t) => ({ kind: "hashtag", term: t.term, count: t.count })),
      ])}
    >
      <div className="grid grid-cols-2 gap-4">
        <div>
          <h4 className="mb-1 text-xs font-medium text-muted">@mentions</h4>
          <TermList items={data.top_mentions} onOpen={(t) => drill(t.drilldown)} />
        </div>
        <div>
          <h4 className="mb-1 text-xs font-medium text-muted">#hashtags</h4>
          <TermList items={data.top_hashtags} onOpen={(t) => drill(t.drilldown)} />
        </div>
      </div>
    </ChartCard>
  );
}

function SwearCard({ data }: { data: WordsResponse }) {
  const drill = useDrill();
  const swear = data.swear!;
  const rows = swear.by_person;
  const option: EChartsOption = {
    grid: defaultGrid(),
    tooltip: { trigger: "axis" as const, axisPointer: { type: "shadow" as const } },
    xAxis: { type: "category" as const, data: rows.map((r) => r.person.name) },
    yAxis: { type: "value" as const, minInterval: 1 },
    series: [
      {
        type: "bar",
        name: "Swear words",
        data: rows.map((r) => ({ value: r.count, itemStyle: { color: personRefColor(r.person) } })),
      },
    ],
  };
  return (
    <ChartCard
      title="Swear words"
      info="Swear words per person (from the built-in list; enabled in Settings)."
      empty={rows.every((r) => r.count === 0)}
      data={recordsToTable(rows.map((r) => ({ person: r.person.name, count: r.count })))}
    >
      <EChart
        option={option}
        height={240}
        onEvents={{ click: (p: { dataIndex: number }) => drill(rows[p.dataIndex]?.drilldown) }}
      />
      {!data.text_hidden && swear.top.length > 0 && (
        <div className="mt-2">
          <h4 className="mb-1 text-xs font-medium text-muted">Most used</h4>
          <TermList items={swear.top.slice(0, 10)} onOpen={(t) => drill(t.drilldown)} />
        </div>
      )}
    </ChartCard>
  );
}
