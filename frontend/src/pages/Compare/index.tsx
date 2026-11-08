import { categoryTable, timeSeriesTable } from "@/charts/tables";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ChartCard } from "@/charts/ChartCard";
import { EChart } from "@/charts/EChart";
import { useApi } from "@/api/useApi";
import { EmptyState } from "@/components/EmptyState";
import { ErrorCard } from "@/components/ErrorCard";
import { PersonBadge } from "@/components/PersonBadge";
import { CloseIcon } from "@/components/icons";
import { SearchSelect, type SearchOption } from "@/features/compare/SearchSelect";
import {
  addId,
  canCompare,
  compareQuery,
  distinctColorIndexes,
  MAX_ITEMS,
  parseCompareState,
  removeId,
  switchMode,
  writeCompareState,
  type CompareMode,
  type CompareState,
} from "@/features/compare/urlState";
import type { Schemas } from "@/features/people/apiTypes";
import { categoryOption, isEmptyChart, timeSeriesOption } from "@/features/people/charts";
import { bestIndex, formatScalar } from "@/features/people/formatScalar";
import { drilldownQuery, useOpenMessages } from "@/lib/drilldown";
import { useDebounced } from "@/features/people/useDebounced";
import { PERSON_PALETTE, personColor } from "@/lib/colors";
import { formatDuration, formatNumber, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

type CompareResponse = Schemas["CompareResponse"];

function usePickerOptions(mode: CompareMode, text: string, selected: number[]) {
  const q = useDebounced(text.trim());
  const chats = useApi(
    "/api/chats",
    { query: { q: q || undefined, sort: "messages", order: "desc", limit: 30 } },
    { enabled: mode === "chats" },
  );
  const people = useApi(
    "/api/people",
    { query: { q: q || undefined, sort: "exchanged", order: "desc", limit: 30 } },
    { enabled: mode === "people" },
  );
  const options: SearchOption[] =
    mode === "chats"
      ? (chats.data?.items ?? []).map((c) => ({
          value: c.thread_id,
          label: c.title,
          hint: `${formatNumber(c.messages)} msgs${c.is_group ? " · group" : ""}`,
          disabled: selected.includes(c.thread_id),
        }))
      : (people.data?.items ?? []).map((p) => ({
          value: p.person.person_id,
          label: p.person.is_owner ? `${p.person.name} (me)` : p.person.name,
          hint: `${formatNumber(p.messages_exchanged)} msgs`,
          icon: (
            <PersonBadge
              name={p.person.name}
              id={p.person.person_id}
              color_index={p.person.color_index}
              size="xs"
            />
          ),
          disabled: selected.includes(p.person.person_id),
        }));
  return { options, loading: mode === "chats" ? chats.isFetching : people.isFetching };
}

/** Names for selected ids (works before the compare response arrives, e.g. one chat). */
function useNames(mode: CompareMode) {
  const threads = useApi("/api/settings/threads", undefined, {
    filters: false,
    enabled: mode === "chats",
  });
  const people = useApi("/api/settings/people", undefined, {
    filters: false,
    enabled: mode === "people",
  });
  return useMemo(() => {
    const m = new Map<number, { name: string; color_index?: number }>();
    if (mode === "chats")
      for (const t of threads.data?.items ?? []) m.set(t.thread_id, { name: t.title });
    else
      for (const p of people.data?.items ?? [])
        m.set(p.person.person_id, { name: p.person.name, color_index: p.person.color_index });
    return m;
  }, [mode, threads.data, people.data]);
}

function Picker({ state, setState }: { state: CompareState; setState: (s: CompareState) => void }) {
  const [text, setText] = useState("");
  const { options, loading } = usePickerOptions(state.mode, text, state.ids);
  const names = useNames(state.mode);
  const full = state.ids.length >= MAX_ITEMS;
  return (
    <section className="card flex flex-col gap-3 p-4" data-print-hide>
      <div className="flex flex-wrap items-center gap-3">
        <div
          role="radiogroup"
          aria-label="Compare"
          className="inline-flex rounded-md border border-border p-0.5"
        >
          {(["chats", "people"] as const).map((m) => (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={state.mode === m}
              className={cn(
                "rounded px-3 py-1 text-sm font-medium",
                state.mode === m ? "bg-accent text-accent-fg" : "text-muted hover:text-fg",
              )}
              onClick={() => setState(switchMode(state, m))}
            >
              {m === "chats" ? "Chats" : "People"}
            </button>
          ))}
        </div>
        <SearchSelect
          className="w-72"
          label={state.mode === "chats" ? "Add a chat" : "Add a person"}
          placeholder={
            full
              ? `Up to ${MAX_ITEMS} items`
              : state.mode === "chats"
                ? "Add a chat…"
                : "Add a person…"
          }
          options={options}
          loading={loading}
          disabled={full}
          onSearch={setText}
          onSelect={(id) => setState(addId(state, id))}
        />
        <span className="text-xs text-muted">Pick 2–{MAX_ITEMS}.</span>
      </div>
      {state.ids.length > 0 && (
        <ul className="flex flex-wrap gap-2" aria-label="Selected">
          {state.ids.map((id) => {
            const info = names.get(id);
            const name = info?.name ?? `#${id}`;
            return (
              <li
                key={id}
                className="inline-flex items-center gap-1.5 rounded-full bg-surface-2 py-1 pl-2 pr-1 text-sm"
              >
                {state.mode === "people" && (
                  <PersonBadge name={name} id={id} color_index={info?.color_index} size="xs" />
                )}
                <Link
                  to={state.mode === "chats" ? `/chats/${id}` : `/people/${id}`}
                  className="max-w-[14rem] truncate hover:text-accent"
                >
                  {name}
                </Link>
                <button
                  type="button"
                  className="icon-btn h-5 w-5"
                  aria-label={`Remove ${name}`}
                  onClick={() => setState(removeId(state, id))}
                >
                  <CloseIcon />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function MetricsTable({ data, colors }: { data: CompareResponse; colors: string[] }) {
  const open = useOpenMessages();
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">Side-by-side metrics</caption>
        <thead className="bg-surface-2 text-left">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">
              Metric
            </th>
            {data.items.map((it, i) => (
              <th key={it.key} scope="col" className="px-3 py-2 text-right font-medium">
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 hover:text-accent"
                  onClick={() => {
                    const q = drilldownQuery(it.drilldown);
                    if (q) open(q);
                  }}
                  title="Open messages"
                >
                  <span
                    className="inline-block h-2.5 w-2.5 rounded-full"
                    style={{ background: colors[i] }}
                    aria-hidden="true"
                  />
                  <span className="max-w-[12rem] truncate">{it.name}</span>
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.metrics.map((m) => {
            const best = bestIndex(m.values, m.lower_is_better);
            return (
              <tr key={m.key} className="border-t border-border">
                <th scope="row" className="px-3 py-1.5 text-left font-normal">
                  {m.label}
                </th>
                {m.values.map((v, i) => (
                  <td
                    key={i}
                    className={cn(
                      "px-3 py-1.5 text-right tabular-nums",
                      best === i && "font-semibold text-accent",
                    )}
                  >
                    {formatScalar(v, m.unit)}
                    {best === i && <span className="sr-only"> (best)</span>}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Charts({
  data,
  colorFor,
}: {
  data: CompareResponse;
  colorFor: (key: string, i: number) => string;
}) {
  const colors = (s: { key: string }, i: number) => colorFor(s.key, i);
  const pct = (v: number) => formatPercent(v);
  const per = (v: number) => formatNumber(v, { maximumFractionDigits: 2 });
  const dur = (v: number) => formatDuration(v / 1000);
  return (
    <>
      <ChartCard
        title="Messages per day"
        info="Average messages per day, by month. Normalized so chats/people of different sizes compare fairly."
        empty={isEmptyChart(data.messages_per_day)}
        data={timeSeriesTable(data.messages_per_day)}
      >
        <EChart option={timeSeriesOption(data.messages_per_day, { format: per, colors })} />
      </ChartCard>
      <div className="card-grid">
        <ChartCard
          title="Hour of day (%)"
          info="Share of messages sent in each hour (local time)."
          empty={isEmptyChart(data.hours)}
          data={categoryTable(data.hours, "Hour")}
          height={280}
        >
          <EChart
            height={280}
            option={categoryOption(data.hours, { format: pct, hours: true, line: true, colors })}
          />
        </ChartCard>
        <ChartCard
          title="Day of week (%)"
          info="Share of messages per weekday."
          empty={isEmptyChart(data.weekdays)}
          data={categoryTable(data.weekdays, "Weekday")}
          height={280}
        >
          <EChart height={280} option={categoryOption(data.weekdays, { format: pct, colors })} />
        </ChartCard>
        <ChartCard
          title="Emoji rate"
          info="Emojis per message, by month."
          empty={isEmptyChart(data.emoji_rate)}
          data={timeSeriesTable(data.emoji_rate)}
          height={280}
        >
          <EChart
            height={280}
            option={timeSeriesOption(data.emoji_rate, { format: per, colors })}
          />
        </ChartCard>
        <ChartCard
          title="Median reply time"
          info="Median time to reply, by month (lower is faster)."
          empty={isEmptyChart(data.reply_median)}
          data={timeSeriesTable(data.reply_median)}
          height={280}
        >
          <EChart
            height={280}
            option={timeSeriesOption(data.reply_median, { format: dur, colors })}
          />
        </ChartCard>
      </div>
    </>
  );
}

export default function ComparePage() {
  const [params, setParams] = useSearchParams();
  const state = parseCompareState(params);
  const setState = (s: CompareState) => setParams(writeCompareState(params, s), { replace: true });
  const ready = canCompare(state);
  const q = useApi<CompareResponse>(
    "/api/compare",
    { query: compareQuery(state) },
    { enabled: ready },
  );
  const data = ready ? q.data : undefined;

  const { colors, colorFor } = useMemo(() => {
    const items = data?.items ?? [];
    const idx = distinctColorIndexes(
      items.map((it) => it.color_index),
      PERSON_PALETTE.length,
    );
    const cs = idx.map((i) => personColor(i));
    const byKey = new Map(items.map((it, i) => [it.key, cs[i]!]));
    return {
      colors: cs,
      colorFor: (key: string, i: number) => byKey.get(key) ?? personColor(i),
    };
  }, [data]);

  return (
    <div className="page">
      <div>
        <h1 className="page-title">Compare</h1>
        <p className="text-sm text-muted">
          Pick 2–{MAX_ITEMS} chats or people to see the same metrics side by side.
        </p>
      </div>
      <Picker state={state} setState={setState} />
      {!ready ? (
        <EmptyState
          title={state.ids.length === 1 ? "Add one more to compare" : "Nothing to compare yet"}
          description={`Choose at least two ${state.mode === "chats" ? "chats" : "people"} above.`}
        />
      ) : q.error ? (
        <ErrorCard error={q.error} onRetry={() => void q.refetch()} />
      ) : !data ? (
        <ChartCard title="Loading…" loading>
          {null}
        </ChartCard>
      ) : (
        <>
          <MetricsTable data={data} colors={colors} />
          <Charts data={data} colorFor={colorFor} />
        </>
      )}
    </div>
  );
}
