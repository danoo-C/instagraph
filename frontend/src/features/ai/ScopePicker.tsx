import { useMemo, useState } from "react";
import type { components } from "@/api/schema";
import { Skeleton } from "@/components/Skeleton";
import { useAllChats } from "@/features/ai/api";
import { formatNumber } from "@/lib/format";

type ChatListItem = components["schemas"]["ChatListResponse"]["items"][number];

const KIND: Record<string, string> = { one_to_one: "1:1", group: "Group", request: "Request" };

/** Searchable checkbox list of chats with quick selects (docs/ai-assistant.md A4.4). */
export function ScopePicker({
  selected,
  onChange,
}: {
  selected: readonly number[];
  onChange: (ids: number[]) => void;
}) {
  const q = useAllChats();
  const [search, setSearch] = useState("");
  const chosen = useMemo(() => new Set(selected), [selected]);
  const items: ChatListItem[] = q.data?.items ?? [];
  const needle = search.trim().toLocaleLowerCase();
  const shown = needle ? items.filter((c) => c.title.toLocaleLowerCase().includes(needle)) : items;

  const toggle = (id: number) => {
    const next = new Set(chosen);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange([...next]);
  };
  const quick: [string, () => number[]][] = [
    ["Top 10", () => items.slice(0, 10).map((c) => c.thread_id)],
    ["All 1:1", () => items.filter((c) => c.kind === "one_to_one").map((c) => c.thread_id)],
    ["All", () => items.map((c) => c.thread_id)],
    ["None", () => []],
  ];

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          className="input min-w-0 flex-1"
          placeholder="Search chats…"
          aria-label="Search chats"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {quick.map(([label, pick]) => (
          <button
            key={label}
            type="button"
            className="btn btn-sm"
            disabled={!q.data}
            onClick={() => onChange(pick())}
          >
            {label}
          </button>
        ))}
      </div>
      <p className="text-xs text-muted">
        {chosen.size} of {items.length} chats allowed. The AI can read only these.
      </p>
      <div className="max-h-72 min-h-0 overflow-y-auto rounded-md border border-border">
        {q.isLoading ? (
          <div className="flex flex-col gap-1 p-2">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} className="h-6 w-full" />
            ))}
          </div>
        ) : (
          <ul>
            {shown.map((c) => (
              <li key={c.thread_id} className="border-t border-border first:border-t-0">
                <label className="flex cursor-pointer items-center gap-2 px-2 py-1.5 text-sm hover:bg-surface-2">
                  <input
                    type="checkbox"
                    checked={chosen.has(c.thread_id)}
                    onChange={() => toggle(c.thread_id)}
                  />
                  <span className="min-w-0 flex-1 truncate">{c.title}</span>
                  <span className="text-xs text-muted">{KIND[c.kind] ?? c.kind}</span>
                  <span className="w-16 text-right text-xs tabular-nums text-muted">
                    {formatNumber(c.messages)}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
