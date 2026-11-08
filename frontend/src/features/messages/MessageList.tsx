import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { cn } from "@/lib/utils";
import type { MessageItem } from "@/features/messages/api";
import { MessageRow } from "@/features/messages/MessageRow";
import type { MediaRef } from "@/features/messages/MediaDialog";
import { localDate } from "@/features/messages/format";

export interface MessageListProps {
  items: readonly MessageItem[];
  hasNext?: boolean;
  hasPrev?: boolean;
  fetchNext?: () => void;
  fetchPrev?: () => void;
  loadingNext?: boolean;
  loadingPrev?: boolean;
  timeZone?: string;
  highlight?: string;
  showThread?: boolean;
  /** Message to highlight and scroll to once (context view). */
  targetId?: number | null;
  onOpenMedia?: (m: MediaRef) => void;
  onContext?: (item: MessageItem) => void;
  /** CSS height of the scroll container. */
  height?: number | string;
  className?: string;
  ariaLabel?: string;
}

const EDGE = 8;

/**
 * Virtualized chat-style message list (TanStack Virtual, dynamic row heights) with
 * cursor pagination in both directions: scrolling near the top/bottom loads more.
 * When older items are prepended the scroll position is kept on the same message.
 */
export function MessageList({
  items,
  hasNext,
  hasPrev,
  fetchNext,
  fetchPrev,
  loadingNext,
  loadingPrev,
  timeZone,
  highlight,
  showThread,
  targetId,
  onOpenMedia,
  onContext,
  height = "70vh",
  className,
  ariaLabel = "Messages",
}: MessageListProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line react-hooks/incompatible-library -- virtualizer used in render only
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 72,
    overscan: 8,
    getItemKey: (i) => items[i]?.message_id ?? i,
  });
  const virtualItems = virtualizer.getVirtualItems();
  const first = virtualItems[0]?.index ?? -1;
  const last = virtualItems[virtualItems.length - 1]?.index ?? -1;

  // Wait for the initial scroll to the target before loading more at the edges.
  const [settled, setSettled] = useState(targetId == null);

  // Load more near the edges.
  const nearEnd = items.length > 0 && last >= items.length - EDGE;
  const nearStart = items.length > 0 && first >= 0 && first <= 2;
  useEffect(() => {
    if (settled && nearEnd && hasNext && !loadingNext) fetchNext?.();
  }, [settled, nearEnd, hasNext, loadingNext, fetchNext, items.length]);
  useEffect(() => {
    if (settled && nearStart && hasPrev && !loadingPrev) fetchPrev?.();
  }, [settled, nearStart, hasPrev, loadingPrev, fetchPrev, items.length]);

  // Keep position when items are prepended.
  const firstIdRef = useRef<number | null>(null);
  useLayoutEffect(() => {
    const prevFirst = firstIdRef.current;
    const now = items[0]?.message_id ?? null;
    firstIdRef.current = now;
    if (prevFirst == null || now == null || prevFirst === now) return;
    const idx = items.findIndex((m) => m.message_id === prevFirst);
    if (idx > 0) virtualizer.scrollToIndex(idx, { align: "start" });
  }, [items, virtualizer]);

  // Scroll to the target once it is in the list.
  const scrolledTo = useRef<number | null>(null);
  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  useEffect(() => {
    if (targetId == null || scrolledTo.current === targetId) return;
    const idx = items.findIndex((m) => m.message_id === targetId);
    if (idx >= 0) {
      scrolledTo.current = targetId;
      // Repeat while rows above the target get measured (dynamic heights).
      // Edge loading is paused until then, so a prepend can't cancel the scroll.
      const timers = [0, 50, 150, 300, 600].map((ms) =>
        setTimeout(() => virtualizer.scrollToIndex(idx, { align: "center" }), ms),
      );
      timers.push(setTimeout(() => setSettled(true), 700));
      timersRef.current = timers;
    } else if (items.length > 0) {
      // Target not in the list (filtered out): don't block paging.
      const t = setTimeout(() => setSettled(true), 0);
      return () => clearTimeout(t);
    }
  }, [targetId, items, virtualizer]);
  useEffect(
    () => () => {
      timersRef.current.forEach(clearTimeout);
      scrolledTo.current = null; // StrictMode remount: schedule the scroll again
    },
    [],
  );

  return (
    <div
      ref={scrollRef}
      className={cn("overflow-auto rounded-lg border border-border bg-bg", className)}
      style={{ height }}
      role="feed"
      aria-label={ariaLabel}
      aria-busy={loadingNext || loadingPrev}
    >
      {loadingPrev && <p className="py-1 text-center text-xs text-muted">Loading earlier…</p>}
      <div style={{ height: virtualizer.getTotalSize(), position: "relative", width: "100%" }}>
        {virtualItems.map((v) => {
          const item = items[v.index]!;
          const prev = items[v.index - 1];
          const day = localDate(item.ts, timeZone);
          const newDay = !prev || localDate(prev.ts, timeZone) !== day;
          const sameRun =
            !newDay &&
            prev != null &&
            prev.sender.person_id === item.sender.person_id &&
            prev.thread_id === item.thread_id &&
            Math.abs(new Date(item.ts).getTime() - new Date(prev.ts).getTime()) < 5 * 60_000;
          return (
            <div
              key={v.key}
              data-index={v.index}
              ref={virtualizer.measureElement}
              className="absolute left-0 top-0 w-full px-2"
              style={{ transform: `translateY(${v.start}px)` }}
              role="article"
              aria-setsize={-1}
            >
              {newDay && (
                <div className="py-2 text-center text-[11px] font-medium text-muted">
                  {new Date(item.ts).toLocaleDateString(undefined, {
                    dateStyle: "full",
                    ...(timeZone ? { timeZone } : {}),
                  })}
                </div>
              )}
              <MessageRow
                item={item}
                timeZone={timeZone}
                highlight={highlight}
                showThread={showThread}
                compact={sameRun}
                active={targetId === item.message_id}
                onOpenMedia={onOpenMedia}
                onContext={onContext}
              />
            </div>
          );
        })}
      </div>
      {loadingNext && <p className="py-2 text-center text-xs text-muted">Loading more…</p>}
      {!hasNext && items.length > 0 && !loadingNext && (
        <p className="py-3 text-center text-xs text-muted">End of results</p>
      )}
    </div>
  );
}
