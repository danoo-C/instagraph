import type { ReactNode } from "react";
import { PersonBadge } from "@/components/PersonBadge";
import { useT } from "@/i18n";
import { formatDateTime } from "@/lib/format";
import { highlightText } from "@/lib/highlight";
import { cn } from "@/lib/utils";

export interface MessageBubbleProps {
  /** Message text — always rendered as plain text (never as HTML, §10). */
  text?: string | null;
  senderName?: string;
  senderId?: string | number | null;
  senderColorIndex?: number | null;
  /** ISO timestamp or epoch ms. */
  timestamp?: string | number | null;
  /** Owner's own message (right-aligned, accent colored). */
  isMe?: boolean;
  reactions?: ReadonlyArray<{ emoji: string; actor?: string }>;
  /** Attachment labels/icons rendered under the text (e.g. "📷 Photo"). */
  attachments?: ReactNode;
  unsent?: boolean;
  /** Highlight occurrences of this term (case-insensitive) using <mark>. */
  highlight?: string;
  /** Time zone for the timestamp (global filter tz). */
  timeZone?: string;
  /** Hide the avatar/name (consecutive messages from one sender). */
  compact?: boolean;
  onClick?: () => void;
  className?: string;
}

/** Chat-style message bubble. */
export function MessageBubble({
  text,
  senderName,
  senderId,
  senderColorIndex,
  timestamp,
  isMe,
  reactions,
  attachments,
  unsent,
  highlight,
  timeZone,
  compact,
  onClick,
  className,
}: MessageBubbleProps) {
  const t = useT();
  const Body = onClick ? "button" : "div";
  return (
    <div className={cn("flex w-full gap-2", isMe ? "flex-row-reverse" : "flex-row", className)}>
      {!isMe && (
        <div className="w-6 shrink-0">
          {!compact && senderName && (
            <PersonBadge name={senderName} id={senderId} color_index={senderColorIndex} size="sm" />
          )}
        </div>
      )}
      <div
        className={cn(
          "flex min-w-0 max-w-[80%] flex-col gap-0.5",
          isMe ? "items-end" : "items-start",
        )}
      >
        {!compact && (senderName || timestamp != null) && (
          <div className="flex gap-2 px-1 text-xs text-muted">
            {senderName && !isMe && <span className="font-medium">{senderName}</span>}
            {timestamp != null && (
              <time
                dateTime={
                  typeof timestamp === "string" ? timestamp : new Date(timestamp).toISOString()
                }
              >
                {formatDateTime(timestamp, timeZone)}
              </time>
            )}
          </div>
        )}
        <Body
          {...(onClick ? { type: "button" as const, onClick } : {})}
          className={cn(
            "whitespace-pre-wrap break-words rounded-2xl px-3 py-2 text-left text-sm",
            isMe ? "bg-accent text-accent-fg" : "bg-surface-2 text-fg",
            unsent && "italic opacity-70",
            onClick && "hover:ring-2 hover:ring-accent/40",
          )}
        >
          {unsent ? t("message.unsent") : text ? highlightText(text, highlight) : null}
          {attachments && <div className="mt-1 text-xs opacity-90">{attachments}</div>}
        </Body>
        {reactions && reactions.length > 0 && (
          <div className="-mt-1 flex flex-wrap gap-0.5 px-1">
            {reactions.map((r, i) => (
              <span
                key={i}
                className="rounded-full border border-border bg-surface px-1.5 text-xs"
                title={r.actor}
              >
                {r.emoji}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
