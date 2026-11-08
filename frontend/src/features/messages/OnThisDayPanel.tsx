import type { QueryParams } from "@/api/client";
import { useApi } from "@/api/useApi";
import { ErrorCard } from "@/components/ErrorCard";
import { Skeleton } from "@/components/Skeleton";
import { formatDate, formatNumber } from "@/lib/format";
import { MessageRow } from "@/features/messages/MessageRow";
import type { MessageItem } from "@/features/messages/api";
import type { MediaRef } from "@/features/messages/MediaDialog";

/** "On this day": messages from today's date in earlier years (§5.9). */
export function OnThisDayPanel({
  query,
  timeZone,
  showThread,
  onShowDay,
  onContext,
  onOpenMedia,
}: {
  query: QueryParams;
  timeZone?: string;
  showThread?: boolean;
  /** Show all messages of that day in the browser. */
  onShowDay: (date: string) => void;
  onContext: (m: MessageItem) => void;
  onOpenMedia: (m: MediaRef) => void;
}) {
  // The date range filter would hide earlier years: on-this-day ignores start/end.
  const q = useApi("/api/messages/on-this-day", {
    query: { ...query, start: undefined, end: undefined, limit: 5 },
  });
  if (q.isLoading) return <Skeleton className="h-40 w-full" />;
  if (q.error) return <ErrorCard error={q.error} onRetry={() => void q.refetch()} />;
  const data = q.data;
  if (!data) return null;
  return (
    <section className="card flex flex-col gap-3 p-4" aria-label="On this day">
      <h3 className="text-sm font-semibold">
        On this day · {formatDate(data.date, { month: "long", day: "numeric" })}
      </h3>
      {data.years.length === 0 && (
        <p className="text-sm text-muted">No messages on this date in earlier years.</p>
      )}
      {data.years.map((y) => (
        <div key={y.year} className="flex flex-col gap-1">
          <div className="flex items-center gap-2 text-sm">
            <span className="font-medium">
              {y.years_ago === 1 ? "1 year ago" : `${y.years_ago} years ago`} · {y.year}
            </span>
            <button type="button" className="btn btn-sm ml-auto" onClick={() => onShowDay(y.date)}>
              Show all {formatNumber(y.count)}
            </button>
          </div>
          {y.messages.map((m) => (
            <MessageRow
              key={m.message_id}
              item={m}
              timeZone={timeZone}
              showThread={showThread}
              onContext={onContext}
              onOpenMedia={onOpenMedia}
            />
          ))}
        </div>
      ))}
    </section>
  );
}
