import { useCallback, useMemo, useState } from "react";
import { apiFetch } from "@/api/client";
import { ErrorCard } from "@/components/ErrorCard";
import { EmptyState } from "@/components/EmptyState";
import { ChartSkeleton } from "@/components/Skeleton";
import { formatNumber } from "@/lib/format";
import { useFilters } from "@/state/filters";
import {
  flattenPages,
  useMessagePages,
  type MessageItem,
  type RandomMessage,
} from "@/features/messages/api";
import { ContextDialog } from "@/features/messages/ContextDialog";
import { FilterPanel } from "@/features/messages/FilterPanel";
import { highlightTerm } from "@/features/messages/format";
import { MediaDialog, type MediaRef } from "@/features/messages/MediaDialog";
import { MessageList } from "@/features/messages/MessageList";
import { OnThisDayPanel } from "@/features/messages/OnThisDayPanel";
import { cursorAt, emptyBrowserQuery, toApiQuery } from "@/features/messages/query";
import { StatsPanel } from "@/features/messages/StatsPanel";
import { useBrowserQuery } from "@/features/messages/useBrowserQuery";

export interface MessageBrowserProps {
  /** Scope to one chat (Messages tab): the thread filter is fixed and hidden. */
  fixedThreadId?: number;
  /** List height (CSS). */
  height?: number | string;
}

/**
 * The Message Browser (§5.9): URL-driven filters, virtualized chat-style results with
 * bidirectional cursor paging, match timeline + per-person counts, jump to context,
 * "On this day" and "Random message".
 */
export function MessageBrowser({ fixedThreadId, height = "70vh" }: MessageBrowserProps) {
  const [query, update] = useBrowserQuery();
  const { filters, setFilters, toQuery } = useFilters();
  const [media, setMedia] = useState<MediaRef | null>(null);
  const [panel, setPanel] = useState<"none" | "onthisday">("none");
  const [randomError, setRandomError] = useState<unknown>(null);
  const [randomBusy, setRandomBusy] = useState(false);

  const effective = useMemo(
    () => (fixedThreadId != null ? { ...query, thread_ids: [fixedThreadId] } : query),
    [query, fixedThreadId],
  );
  const apiQuery = useMemo(() => toApiQuery(effective), [effective]);
  const pages = useMessagePages({
    query: apiQuery,
    order: query.order,
    startCursor: query.at != null ? cursorAt(query.at, query.order) : null,
  });
  const items = useMemo(() => flattenPages(pages.data?.pages), [pages.data]);
  const firstPage = pages.data?.pages[0];
  const total = firstPage?.total;
  const stats = pages.data?.pages.find((p) => p.stats)?.stats;
  const highlight = highlightTerm(query.text, query.text_mode);
  const showThread = fixedThreadId == null && effective.thread_ids.length !== 1;

  const openContext = useCallback((m: MessageItem) => update({ context: m.message_id }), [update]);
  const closeContext = useCallback(() => update({ context: null }), [update]);
  const { fetchNextPage, fetchPreviousPage } = pages;
  const fetchNext = useCallback(() => void fetchNextPage(), [fetchNextPage]);
  const fetchPrev = useCallback(() => void fetchPreviousPage(), [fetchPreviousPage]);

  const random = async () => {
    setRandomBusy(true);
    setRandomError(null);
    try {
      const r = await apiFetch<RandomMessage>("GET", "/api/messages/random", {
        query: { ...toQuery(), ...apiQuery, seed: Math.floor(Math.random() * 2 ** 31) },
      });
      if (r.message) update({ context: r.message.message_id });
      else setRandomError(new Error("No message matches the current filters."));
    } catch (e) {
      setRandomError(e);
    } finally {
      setRandomBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <FilterPanel query={query} update={update} fixedThreadId={fixedThreadId} />

      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm text-muted" aria-live="polite">
          {total != null
            ? `${formatNumber(total)} ${total === 1 ? "message" : "messages"}`
            : pages.isLoading
              ? "Searching…"
              : ""}
        </p>
        <div className="ml-auto flex gap-2">
          <button
            type="button"
            className="btn btn-sm"
            aria-pressed={panel === "onthisday"}
            onClick={() => setPanel((p) => (p === "onthisday" ? "none" : "onthisday"))}
          >
            📅 On this day
          </button>
          <button type="button" className="btn btn-sm" onClick={random} disabled={randomBusy}>
            🎲 Random message
          </button>
        </div>
      </div>
      {randomError != null && (
        <ErrorCard error={randomError} onRetry={() => setRandomError(null)} />
      )}

      {panel === "onthisday" && (
        <OnThisDayPanel
          query={apiQuery}
          timeZone={filters.tz}
          showThread={showThread}
          onShowDay={(d) => {
            setFilters({ start: d, end: d });
            setPanel("none");
          }}
          onContext={openContext}
          onOpenMedia={setMedia}
        />
      )}

      <StatsPanel
        stats={stats}
        total={total}
        loading={pages.isLoading}
        onRange={(start, end) => setFilters({ start, end })}
        onSender={(id) => update({ sender_ids: [id] })}
      />

      {pages.isLoading ? (
        <ChartSkeleton height={400} />
      ) : pages.error && !pages.data ? (
        <ErrorCard error={pages.error} onRetry={() => void pages.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          title="No messages match"
          description="Try removing a filter or widening the date range."
        />
      ) : (
        <MessageList
          items={items}
          hasNext={pages.hasNextPage}
          hasPrev={pages.hasPreviousPage}
          fetchNext={fetchNext}
          fetchPrev={fetchPrev}
          loadingNext={pages.isFetchingNextPage}
          loadingPrev={pages.isFetchingPreviousPage}
          timeZone={filters.tz}
          highlight={highlight}
          showThread={showThread}
          targetId={query.at}
          onOpenMedia={setMedia}
          onContext={openContext}
          height={height}
          ariaLabel="Search results"
        />
      )}

      <ContextDialog
        messageId={query.context}
        onClose={closeContext}
        onOpenMedia={setMedia}
        highlight={highlight}
        onShowInList={(id, tid) =>
          update({
            ...emptyBrowserQuery(),
            order: "asc",
            thread_ids: [fixedThreadId ?? tid],
            at: id,
          })
        }
      />
      <MediaDialog media={media} onClose={() => setMedia(null)} />
    </div>
  );
}
