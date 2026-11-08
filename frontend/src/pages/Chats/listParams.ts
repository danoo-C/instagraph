import type { SortingState } from "@/components/dataTableColumns";
import type { ChatSortKey } from "@/features/chat/types";

export const PAGE_SIZE = 100;

export const SORT_KEYS: readonly ChatSortKey[] = [
  "title",
  "kind",
  "participants",
  "messages",
  "my_share",
  "messages_last_30d",
  "first_ts",
  "last_ts",
  "duration_days",
  "avg_per_day",
  "median_reply_me_ms",
  "median_reply_them_ms",
  "emojis",
  "calls",
  "trend_slope",
];

export interface ListParams {
  q: string;
  sort: ChatSortKey;
  order: "asc" | "desc";
}

/** Read the chats-list params (`q`, `sort`, `order`) from the URL with defaults. */
export function parseListParams(p: URLSearchParams): ListParams {
  const s = p.get("sort") as ChatSortKey | null;
  const o = p.get("order");
  return {
    q: p.get("q") ?? "",
    sort: s && SORT_KEYS.includes(s) ? s : "messages",
    order: o === "asc" ? "asc" : "desc",
  };
}

/** TanStack sorting state → backend sort params (null = default). */
export function sortingToParams(
  s: SortingState,
): { sort: ChatSortKey; order: "asc" | "desc" } | null {
  const first = s[0];
  if (!first || !SORT_KEYS.includes(first.id as ChatSortKey)) return null;
  return { sort: first.id as ChatSortKey, order: first.desc ? "desc" : "asc" };
}
