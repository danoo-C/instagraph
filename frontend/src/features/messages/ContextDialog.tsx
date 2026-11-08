import { useCallback, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { apiFetch } from "@/api/client";
import { useApi } from "@/api/useApi";
import { ErrorCard } from "@/components/ErrorCard";
import { CloseIcon } from "@/components/icons";
import { ChartSkeleton } from "@/components/Skeleton";
import { useFilters } from "@/state/filters";
import type { MessageItem, MessagePage } from "@/features/messages/api";
import { MessageList } from "@/features/messages/MessageList";
import type { MediaRef } from "@/features/messages/MediaDialog";
import { encodeCursor } from "@/features/messages/query";

const STEP = 50;

/** "Jump to context": the thread around one message, target highlighted (§5.9). */
export function ContextDialog({
  messageId,
  onClose,
  onOpenMedia,
  onShowInList,
  highlight,
}: {
  messageId: number | null;
  onClose: () => void;
  onOpenMedia?: (m: MediaRef) => void;
  /** Show the whole chat in the browser list, starting at this message. */
  onShowInList?: (messageId: number, threadId: number) => void;
  highlight?: string;
}) {
  return (
    <Dialog.Root open={messageId != null} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/50" />
        <Dialog.Content className="card fixed inset-x-2 inset-y-4 z-50 mx-auto flex max-w-3xl flex-col gap-3 p-4 sm:inset-y-8">
          {messageId != null && (
            <ContextBody
              key={messageId}
              messageId={messageId}
              onOpenMedia={onOpenMedia}
              onShowInList={onShowInList}
              highlight={highlight}
            />
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function ContextBody({
  messageId,
  onOpenMedia,
  onShowInList,
  highlight,
}: {
  messageId: number;
  onOpenMedia?: (m: MediaRef) => void;
  onShowInList?: (messageId: number, threadId: number) => void;
  highlight?: string;
}) {
  const { filters } = useFilters();
  const q = useApi("/api/messages/{message_id}/context", {
    path: { message_id: messageId },
    query: { before: 25, after: 25 },
  });
  const [before, setBefore] = useState<MessageItem[]>([]);
  const [after, setAfter] = useState<MessageItem[]>([]);
  const [moreBefore, setMoreBefore] = useState<boolean | null>(null);
  const [moreAfter, setMoreAfter] = useState<boolean | null>(null);
  const [loading, setLoading] = useState<"before" | "after" | null>(null);
  const [error, setError] = useState<unknown>(null);

  const data = q.data;
  const items = data ? [...before, ...data.items, ...after] : [];
  const hasPrev = moreBefore ?? data?.has_more_before ?? false;
  const hasNext = moreAfter ?? data?.has_more_after ?? false;

  const load = useCallback(
    async (direction: "before" | "after") => {
      if (!data || loading) return;
      const all = [...before, ...data.items, ...after];
      const pivot = direction === "before" ? all[0] : all[all.length - 1];
      if (!pivot) return;
      setLoading(direction);
      try {
        // Same scope as the context endpoint: whole thread, only exclude_system applies.
        const page = await apiFetch<MessagePage>("GET", "/api/messages", {
          query: {
            thread_id: data.thread_id,
            order: "asc",
            cursor: encodeCursor(0, pivot.message_id),
            direction,
            limit: STEP,
            tz: filters.tz,
            exclude_system: filters.exclude_system,
            include_groups: true,
            include_requests: true,
            include_archived: true,
          },
        });
        if (direction === "before") {
          setBefore((b) => [...page.items, ...b]);
          setMoreBefore(page.has_prev);
        } else {
          setAfter((a) => [...a, ...page.items]);
          setMoreAfter(page.has_next);
        }
      } catch (e) {
        setError(e);
      } finally {
        setLoading(null);
      }
    },
    [data, before, after, loading, filters.tz, filters.exclude_system],
  );

  return (
    <>
      <div className="flex items-center gap-2">
        <Dialog.Title className="truncate font-semibold">
          {data ? `Context in “${data.thread_title}”` : "Context"}
        </Dialog.Title>
        <Dialog.Description className="sr-only">
          Messages before and after the selected message; the selected message is highlighted.
        </Dialog.Description>
        {data && onShowInList && (
          <button
            type="button"
            className="btn btn-sm ml-auto"
            title="Clear the other filters and scroll through the whole chat from here"
            onClick={() => onShowInList(messageId, data.thread_id)}
          >
            Browse chat from here
          </button>
        )}
        <Dialog.Close asChild>
          <button
            type="button"
            className={data && onShowInList ? "icon-btn" : "icon-btn ml-auto"}
            aria-label="Close"
          >
            <CloseIcon />
          </button>
        </Dialog.Close>
      </div>
      {q.isLoading ? (
        <ChartSkeleton height={400} />
      ) : q.error ? (
        <ErrorCard error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <>
          {error != null && <ErrorCard error={error} onRetry={() => setError(null)} />}
          <MessageList
            items={items}
            hasPrev={hasPrev && error == null}
            hasNext={hasNext && error == null}
            fetchPrev={() => void load("before")}
            fetchNext={() => void load("after")}
            loadingPrev={loading === "before"}
            loadingNext={loading === "after"}
            timeZone={filters.tz}
            targetId={messageId}
            highlight={highlight}
            onOpenMedia={onOpenMedia}
            height="100%"
            className="min-h-0 flex-1"
            ariaLabel="Messages around the selected message"
          />
        </>
      )}
    </>
  );
}
