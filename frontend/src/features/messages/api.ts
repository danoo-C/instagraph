import { useInfiniteQuery, type InfiniteData } from "@tanstack/react-query";
import { apiFetch, type QueryParams } from "@/api/client";
import type { ApiError } from "@/api/errors";
import type { components } from "@/api/schema";
import { apiQueryKey, useRedirectOnNoData } from "@/api/useApi";
import { useFilters } from "@/state/filters";
import type { SortOrder } from "@/features/messages/query";

type S = components["schemas"];
export type MessageItem = S["MessageItem"];
export type MessagePage = S["MessagePage"];
export type MessageContext = S["MessageContext"];
export type OnThisDay = S["OnThisDay"];
export type RandomMessage = S["RandomMessage"];
export type SearchStats = S["SearchStats"];

export interface PageParam {
  cursor: string;
  direction: "after" | "before";
}

export const PAGE_SIZE = 100;

/**
 * Bidirectional cursor pagination over `GET /api/messages` (TanStack infinite query).
 * `next` continues in display order, `previous` goes back before the first item.
 * Stats (`include_stats`) are requested with the first page only.
 */
export function useMessagePages(opts: {
  query: QueryParams;
  order: SortOrder;
  /** Initial cursor (e.g. start the list at a message); null = start of results. */
  startCursor?: string | null;
  /** Merge global filters (default true). */
  filters?: boolean;
  enabled?: boolean;
  limit?: number;
}) {
  const { toQuery } = useFilters();
  const merged: QueryParams = {
    ...(opts.filters === false ? {} : toQuery()),
    ...opts.query,
    order: opts.order,
    limit: opts.limit ?? PAGE_SIZE,
  };
  const initial: PageParam | null = opts.startCursor
    ? { cursor: opts.startCursor, direction: "after" }
    : null;
  const result = useInfiniteQuery<
    MessagePage,
    ApiError,
    InfiniteData<MessagePage, PageParam | null>,
    ReturnType<typeof apiQueryKey>,
    PageParam | null
  >({
    queryKey: apiQueryKey("/api/messages", undefined, {
      ...merged,
      __browser: true,
      __start: opts.startCursor ?? null,
    }),
    initialPageParam: initial,
    queryFn: ({ signal, pageParam }) =>
      apiFetch<MessagePage>("GET", "/api/messages", {
        query: {
          ...merged,
          ...(pageParam ? { cursor: pageParam.cursor, direction: pageParam.direction } : {}),
          ...(isInitial(pageParam, opts.startCursor) ? { include_stats: true } : {}),
        },
        signal,
      }),
    getNextPageParam: (last) =>
      last.has_next && last.next_cursor
        ? { cursor: last.next_cursor, direction: "after" }
        : undefined,
    getPreviousPageParam: (first) =>
      first.has_prev && first.prev_cursor
        ? { cursor: first.prev_cursor, direction: "before" }
        : undefined,
    enabled: opts.enabled ?? true,
  });
  useRedirectOnNoData(result.error);
  return result;
}

function isInitial(p: PageParam | null, start: string | null | undefined): boolean {
  return p == null || (p.direction === "after" && p.cursor === start);
}

/** Flatten pages into one list (pages are already in display order). */
export function flattenPages(pages: readonly MessagePage[] | undefined): MessageItem[] {
  if (!pages) return [];
  const seen = new Set<number>();
  const out: MessageItem[] = [];
  for (const p of pages)
    for (const m of p.items) {
      if (seen.has(m.message_id)) continue;
      seen.add(m.message_id);
      out.push(m);
    }
  return out;
}

/** Media URLs (served only by id, §10). */
export const mediaUrl = (id: number) => `/api/media/${id}`;
export const thumbUrl = (id: number, size?: number) =>
  `/api/media/${id}/thumb${size ? `?size=${size}` : ""}`;
