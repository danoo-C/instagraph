import { timeSeriesTable } from "@/charts/tables";
import { useEffect, useState, type KeyboardEvent } from "react";
import { keepPreviousData, useMutation, useQueries } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import type { ApiError } from "@/api/errors";
import { queryClient } from "@/api/queryClient";
import { apiQueryKey, useApi } from "@/api/useApi";
import type { components } from "@/api/schema";
import { ChartCard } from "@/charts/ChartCard";
import { EChart } from "@/charts/EChart";
import { defaultGrid, timeSeriesZoom } from "@/charts/sampling";
import { ErrorCard } from "@/components/ErrorCard";
import { PersonBadge } from "@/components/PersonBadge";
import { bucketEnd, timeSeriesToEcharts } from "@/features/tabs/charts";
import { useDrill, useOpenContext } from "@/lib/drilldown";
import { SelectInput } from "@/components/SelectInput";
import {
  addTerms,
  MAX_TRACKED_TERMS,
  parseTermInput,
  withSavedTerms,
  type TermMode,
} from "@/features/tabs/words";
import { formatDate, formatNumber } from "@/lib/format";
import { useFilters } from "@/state/filters";

type TermsResponse = components["schemas"]["TermsResponse"];
type TermResult = components["schemas"]["TermResult"];
type MessageRef = components["schemas"]["MessageRef"];

const MODES: { value: TermMode; label: string }[] = [
  { value: "word", label: "Whole word" },
  { value: "plain", label: "Substring" },
  { value: "regex", label: "Regex" },
];

/**
 * Term tracker (§7.2 Words #10). One request per term (`GET /api/chats/{id}/terms?q=…`):
 * useApi joins array params with commas, which the backend would split, and a regex may
 * itself contain commas — so each chip is sent on its own.
 */
export function TermTracker({ chatId, savedTerms }: { chatId: number; savedTerms: string[] }) {
  const [terms, setTerms] = useState<string[]>(savedTerms);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<TermMode>("word");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [accentInsensitive, setAccentInsensitive] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const { toQuery } = useFilters();
  const drill = useDrill();
  const openContext = useOpenContext();

  // Re-seed chips when the saved terms change (e.g. after a refetch) and user hasn't typed.
  const savedKey = savedTerms.join("\u0000");
  const [seededKey, setSeededKey] = useState(savedKey);
  if (seededKey !== savedKey) {
    setSeededKey(savedKey);
    setTerms(savedTerms);
  }

  const filterQuery = toQuery();
  const results = useQueries({
    queries: terms.map((term) => {
      const query = {
        ...filterQuery,
        q: term,
        mode,
        case_sensitive: caseSensitive,
        accent_insensitive: accentInsensitive,
      };
      const path = "/api/chats/{chat_id}/terms";
      const pathParams = { chat_id: chatId };
      return {
        queryKey: apiQueryKey(path, pathParams, query),
        queryFn: ({ signal }: { signal: AbortSignal }) =>
          apiFetch<TermsResponse>("GET", path, { path: pathParams, query, signal }),
        placeholderData: keepPreviousData,
      };
    }),
  });
  const loaded: TermResult[] = results.flatMap((r) => r.data?.terms.slice(0, 1) ?? []);
  const firstError = results.find((r) => r.error)?.error as ApiError | undefined;
  const loading = results.some((r) => r.isLoading);
  const current = loaded.find((t) => t.term === selected) ?? loaded[0];

  const add = (raw: string) => {
    const parsed = parseTermInput(raw, mode);
    if (parsed.length) setTerms((t) => addTerms(t, parsed));
    setInput("");
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || (e.key === "," && mode !== "regex")) {
      e.preventDefault();
      add(input);
    } else if (e.key === "Backspace" && !input && terms.length) {
      setTerms((t) => t.slice(0, -1));
    }
  };

  // ---------------------------------------------------------------- saving (settings.custom_terms)
  const settings = useApi("/api/settings", undefined, { filters: false });
  const threads = useApi("/api/settings/threads", undefined, { filters: false });
  const folder = threads.data?.items.find((t) => t.thread_id === chatId)?.folder ?? null;
  const redacted = settings.data?.redacted ?? false;
  const canSave = Boolean(settings.data && folder && !redacted);
  const saveDisabledReason = redacted
    ? "Saving terms is disabled in anonymize mode."
    : settings.data && threads.data && !folder
      ? "This chat has no stable folder id, so terms can't be saved."
      : undefined;
  const save = useMutation({
    mutationFn: () =>
      apiFetch("PUT", "/api/settings", {
        body: {
          custom_terms: withSavedTerms(settings.data?.settings.custom_terms, folder!, terms),
        },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["api", "/api/settings"] });
      void queryClient.invalidateQueries({ queryKey: ["api", "/api/chats/{chat_id}/words"] });
    },
  });
  const isSaved = terms.join("\u0000") === savedKey;
  const { reset } = save;
  useEffect(() => {
    reset();
  }, [terms, reset]);

  // ---------------------------------------------------------------- chart
  const option = (() => {
    const series = timeSeriesToEcharts(current?.timeline, { type: "line" });
    return {
      grid: defaultGrid({ slider: true, legend: true }),
      legend: { top: 0 },
      tooltip: { trigger: "axis" as const },
      dataZoom: timeSeriesZoom(),
      xAxis: { type: "time" as const },
      yAxis: { type: "value" as const, minInterval: 1 },
      series,
    };
  })();

  return (
    <section className="card flex flex-col gap-3 p-4" aria-label="Term tracker">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">Term tracker</h3>
        <span className="text-xs text-muted">
          Track words, phrases or regular expressions over time (up to {MAX_TRACKED_TERMS}).
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="input flex min-h-9 min-w-64 flex-1 flex-wrap items-center gap-1 py-1">
          {terms.map((t) => (
            <span
              key={t}
              className="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-xs"
            >
              <span className="font-mono">{t}</span>
              <button
                type="button"
                className="text-muted hover:text-danger"
                aria-label={`Remove ${t}`}
                onClick={() => setTerms((list) => list.filter((x) => x !== t))}
              >
                ×
              </button>
            </span>
          ))}
          <input
            aria-label="Add term"
            className="min-w-32 flex-1 bg-transparent text-sm outline-none"
            placeholder={
              terms.length >= MAX_TRACKED_TERMS
                ? "Term limit reached"
                : mode === "regex"
                  ? "Regex, then Enter"
                  : "Type a term, Enter or comma to add"
            }
            disabled={terms.length >= MAX_TRACKED_TERMS}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey}
            onBlur={() => input.trim() && add(input)}
          />
        </div>
        <SelectInput label="Match mode" value={mode} onChange={(v) => setMode(v as TermMode)}>
          {MODES.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </SelectInput>
        <label className="flex items-center gap-1 text-sm">
          <input
            type="checkbox"
            checked={caseSensitive}
            onChange={(e) => setCaseSensitive(e.target.checked)}
          />
          Case-sensitive
        </label>
        <label className="flex items-center gap-1 text-sm">
          <input
            type="checkbox"
            checked={accentInsensitive}
            onChange={(e) => setAccentInsensitive(e.target.checked)}
          />
          Ignore accents
        </label>
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={!canSave || save.isPending || isSaved}
          title={saveDisabledReason ?? "Save these terms for this chat (Settings)"}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "Saving…" : isSaved ? "Saved" : "Save terms"}
        </button>
      </div>
      {saveDisabledReason && <p className="text-xs text-muted">{saveDisabledReason}</p>}
      {save.error && <ErrorCard error={save.error} title="Could not save terms" />}
      {firstError && <ErrorCard error={firstError} />}

      {terms.length === 0 ? (
        <p className="text-sm text-muted">Add a term to see how often each person uses it.</p>
      ) : (
        <>
          <ChartCard
            title={current ? `“${current.term}” over time` : "Term usage over time"}
            info="Occurrences of the term per time bucket, one line per person. Click a point to open those messages."
            loading={loading && !current}
            empty={!loading && !current}
            data={current ? timeSeriesTable(current.timeline) : null}
            actions={
              loaded.length > 1 ? (
                <SelectInput
                  label="Term"
                  value={current?.term ?? ""}
                  onChange={setSelected}
                  className="max-w-40"
                >
                  {loaded.map((t) => (
                    <option key={t.term} value={t.term}>
                      {t.term}
                    </option>
                  ))}
                </SelectInput>
              ) : undefined
            }
          >
            <EChart
              option={option}
              onEvents={{
                click: (p: { seriesIndex: number; value: [string, number] }) => {
                  if (!current) return;
                  const s = current.timeline.series[p.seriesIndex];
                  const start = String(p.value[0]).slice(0, 10);
                  drill(current.drilldown, {
                    ...(s?.person_id != null ? { sender_id: s.person_id } : {}),
                    start,
                    end: bucketEnd(start, current.timeline.bucket),
                  });
                },
              }}
            />
          </ChartCard>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Term totals</caption>
              <thead className="text-xs text-muted">
                <tr>
                  <th scope="col" className="px-2 py-1 font-medium">
                    Term
                  </th>
                  <th scope="col" className="px-2 py-1 font-medium">
                    Total
                  </th>
                  <th scope="col" className="px-2 py-1 font-medium">
                    Per person
                  </th>
                  <th scope="col" className="px-2 py-1 font-medium">
                    First use
                  </th>
                  <th scope="col" className="px-2 py-1 font-medium">
                    Last use
                  </th>
                  <th scope="col" className="px-2 py-1">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {loaded.map((t) => (
                  <tr key={t.term} className="border-t border-border align-top">
                    <td className="px-2 py-1.5 font-mono">{t.term}</td>
                    <td className="px-2 py-1.5 tabular-nums">
                      {formatNumber(t.occurrences)}
                      <span className="block text-xs text-muted">
                        in {formatNumber(t.messages)} messages
                      </span>
                    </td>
                    <td className="px-2 py-1.5">
                      <div className="flex flex-wrap gap-2">
                        {t.by_person.map((pc) => (
                          <button
                            key={pc.person.person_id}
                            type="button"
                            className="inline-flex items-center gap-1 rounded hover:text-accent"
                            onClick={() => drill(pc.drilldown)}
                            title="Show these messages"
                          >
                            <PersonBadge
                              name={pc.person.name}
                              id={pc.person.person_id}
                              color_index={pc.person.color_index}
                              size="xs"
                            />
                            <span className="tabular-nums">{formatNumber(pc.count)}</span>
                          </button>
                        ))}
                      </div>
                    </td>
                    <td className="px-2 py-1.5">
                      <UseRef msg={t.first} onOpen={openContext} />
                    </td>
                    <td className="px-2 py-1.5">
                      <UseRef msg={t.last} onOpen={openContext} />
                    </td>
                    <td className="px-2 py-1.5">
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={() => drill(t.drilldown)}
                        disabled={t.messages === 0}
                      >
                        Show occurrences
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

function UseRef({
  msg,
  onOpen,
}: {
  msg: MessageRef | null | undefined;
  onOpen: (id: number, thread?: number) => void;
}) {
  if (!msg) return <span className="text-muted">—</span>;
  return (
    <button
      type="button"
      className="max-w-56 text-left hover:text-accent"
      onClick={() => onOpen(msg.message_id, msg.thread_id)}
      title="Jump to context"
    >
      <span className="block text-xs text-muted">
        {formatDate(msg.ts)} · {msg.sender.name}
      </span>
      <span className="line-clamp-2 text-xs">{msg.text ?? "(text hidden)"}</span>
    </button>
  );
}
