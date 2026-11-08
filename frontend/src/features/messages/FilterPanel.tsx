import { useState, type FormEvent, type ReactNode } from "react";
import { useApi } from "@/api/useApi";
import { CloseIcon } from "@/components/icons";
import { useFilters } from "@/state/filters";
import {
  activeFilterCount,
  emptyBrowserQuery,
  extraLabel,
  MESSAGE_TYPES,
  TEXT_MODES,
  type BrowserQuery,
  type TextMode,
} from "@/features/messages/query";

const MODE_LABELS: Record<TextMode, string> = {
  plain: "Contains",
  word: "Whole word",
  regex: "Regex",
};

/** Filters of the Message Browser (§5.9). Dates are the global start/end filters. */
export function FilterPanel({
  query,
  update,
  fixedThreadId,
}: {
  query: BrowserQuery;
  update: (patch: Partial<BrowserQuery>, opts?: { replace?: boolean }) => void;
  fixedThreadId?: number;
}) {
  const { filters, setFilters } = useFilters();
  const threadId =
    fixedThreadId ?? (query.thread_ids.length === 1 ? query.thread_ids[0] : undefined);

  const chats = useApi(
    "/api/chats",
    { query: { limit: 500, sort: "messages", order: "desc" } },
    { enabled: fixedThreadId == null, staleTime: 60_000 },
  );
  const header = useApi(
    "/api/chats/{chat_id}",
    { path: { chat_id: threadId ?? 0 } },
    { enabled: threadId != null, staleTime: 60_000 },
  );
  const people = useApi(
    "/api/people",
    { query: { limit: 500 } },
    { enabled: threadId == null, staleTime: 60_000 },
  );
  const senders =
    threadId != null
      ? (header.data?.participants ?? [])
      : (people.data?.items.map((p) => p.person) ?? []);

  // Text is applied on submit (avoids a request per keystroke and half-typed regexes).
  const [text, setText] = useState(query.text);
  const [syncedText, setSyncedText] = useState(query.text);
  if (syncedText !== query.text) {
    // URL changed (drilldown, clear, back button): adopt it.
    setSyncedText(query.text);
    setText(query.text);
  }
  const submit = (e: FormEvent) => {
    e.preventDefault();
    update({ text: text.trim() });
  };

  const active = activeFilterCount(query, fixedThreadId != null);
  const extras = Object.entries(query.extra);

  return (
    <div className="card flex flex-col gap-3 p-3">
      <form onSubmit={submit} className="flex flex-wrap items-center gap-2" role="search">
        <label className="sr-only" htmlFor="mb-text">
          Search text
        </label>
        <input
          id="mb-text"
          className="input min-w-[12rem] flex-1"
          type="search"
          placeholder={query.text_mode === "regex" ? "Regular expression…" : "Search messages…"}
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={500}
        />
        <select
          aria-label="Match mode"
          className="input w-auto"
          value={query.text_mode}
          onChange={(e) => update({ text_mode: e.target.value as TextMode, text: text.trim() })}
        >
          {TEXT_MODES.map((m) => (
            <option key={m} value={m}>
              {MODE_LABELS[m]}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1 text-sm">
          <input
            type="checkbox"
            checked={query.case_sensitive}
            onChange={(e) => update({ case_sensitive: e.target.checked, text: text.trim() })}
          />
          Aa
          <span className="sr-only">Case-sensitive</span>
        </label>
        <label className="flex items-center gap-1 text-sm" title="ahoj = áhoj">
          <input
            type="checkbox"
            checked={query.accent_insensitive}
            onChange={(e) => update({ accent_insensitive: e.target.checked, text: text.trim() })}
          />
          Ignore accents
        </label>
        <button type="submit" className="btn btn-primary btn-sm">
          Search
        </button>
      </form>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {fixedThreadId == null && (
          <Field label="Chat">
            <select
              className="input"
              value={query.thread_ids.length > 1 ? "multi" : String(query.thread_ids[0] ?? "")}
              onChange={(e) =>
                update({
                  thread_ids: e.target.value ? [Number(e.target.value)] : [],
                  sender_ids: [],
                })
              }
            >
              <option value="">All chats</option>
              {query.thread_ids.length > 1 && (
                <option value="multi">{query.thread_ids.length} chats</option>
              )}
              {chats.data?.items.map((c) => (
                <option key={c.thread_id} value={c.thread_id}>
                  {c.title}
                </option>
              ))}
              {threadId != null && !chats.data?.items.some((c) => c.thread_id === threadId) && (
                <option value={threadId}>{header.data?.title ?? `Chat ${threadId}`}</option>
              )}
            </select>
          </Field>
        )}
        <Field label="Sender">
          <select
            className="input"
            value={query.sender_ids.length > 1 ? "multi" : String(query.sender_ids[0] ?? "")}
            onChange={(e) => update({ sender_ids: e.target.value ? [Number(e.target.value)] : [] })}
          >
            <option value="">Anyone</option>
            {query.sender_ids.length > 1 && (
              <option value="multi">{query.sender_ids.length} people</option>
            )}
            {senders.map((p) => (
              <option key={p.person_id} value={p.person_id}>
                {p.name}
                {p.is_owner ? " (me)" : ""}
              </option>
            ))}
            {query.sender_ids.length === 1 &&
              !senders.some((p) => p.person_id === query.sender_ids[0]) && (
                <option value={query.sender_ids[0]}>Person {query.sender_ids[0]}</option>
              )}
          </select>
        </Field>
        <Field label="Type">
          <select
            className="input"
            value={query.types.length > 1 ? "multi" : (query.types[0] ?? "")}
            onChange={(e) => update({ types: e.target.value ? [e.target.value] : [] })}
          >
            <option value="">Any type</option>
            {query.types.length > 1 && <option value="multi">{query.types.join(", ")}</option>}
            {MESSAGE_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Reactions">
          <select
            className="input"
            value={query.has_reactions == null ? "" : String(query.has_reactions)}
            onChange={(e) =>
              update({ has_reactions: e.target.value === "" ? null : e.target.value === "true" })
            }
          >
            <option value="">Any</option>
            <option value="true">With reactions</option>
            <option value="false">Without reactions</option>
          </select>
        </Field>
        <Field label="Contains emoji">
          <input
            className="input"
            value={query.emoji}
            placeholder="e.g. 😂"
            maxLength={16}
            onChange={(e) => update({ emoji: e.target.value.trim() }, { replace: true })}
          />
        </Field>
        <Field label="From">
          <input
            type="date"
            className="input"
            value={filters.start ?? ""}
            max={filters.end ?? undefined}
            onChange={(e) => setFilters({ start: e.target.value || null })}
          />
        </Field>
        <Field label="To">
          <input
            type="date"
            className="input"
            value={filters.end ?? ""}
            min={filters.start ?? undefined}
            onChange={(e) => setFilters({ end: e.target.value || null })}
          />
        </Field>
        <Field label="Order">
          <select
            className="input"
            value={query.order}
            onChange={(e) => update({ order: e.target.value === "asc" ? "asc" : "desc" })}
          >
            <option value="desc">Newest first</option>
            <option value="asc">Oldest first</option>
          </select>
        </Field>
      </div>

      {(extras.length > 0 || active > 0) && (
        <div className="flex flex-wrap items-center gap-2">
          {extras.map(([k, v]) => (
            <span
              key={k}
              className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-2 px-2 py-0.5 text-xs"
            >
              {extraLabel(k, v)}
              <button
                type="button"
                className="rounded-full hover:text-danger"
                aria-label={`Remove filter ${extraLabel(k, v)}`}
                onClick={() => {
                  const next = { ...query.extra };
                  delete next[k];
                  update({ extra: next });
                }}
              >
                <CloseIcon width={12} height={12} />
              </button>
            </span>
          ))}
          {active > 0 && (
            <button
              type="button"
              className="btn btn-sm ml-auto"
              onClick={() =>
                update({
                  ...emptyBrowserQuery(),
                  order: query.order,
                  thread_ids: fixedThreadId != null ? query.thread_ids : [],
                })
              }
            >
              Clear filters ({active})
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-xs font-medium text-muted">
      {label}
      {children}
    </label>
  );
}
