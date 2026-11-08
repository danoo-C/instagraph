import { memo } from "react";
import { MessageBubble } from "@/components/MessageBubble";
import { cn } from "@/lib/utils";
import { thumbUrl, mediaUrl, type MessageItem } from "@/features/messages/api";
import {
  attachmentLabels,
  callLabel,
  safeHref,
  shareLabel,
  systemLabel,
} from "@/features/messages/format";
import type { MediaRef } from "@/features/messages/MediaDialog";

export interface MessageRowProps {
  item: MessageItem;
  timeZone?: string;
  highlight?: string;
  /** Show the chat title above the bubble (cross-chat results). */
  showThread?: boolean;
  /** Hide avatar/name/time (consecutive message from the same sender). */
  compact?: boolean;
  /** Target message in the context view. */
  active?: boolean;
  onOpenMedia?: (m: MediaRef) => void;
  /** "Jump to context" action. */
  onContext?: (item: MessageItem) => void;
}

/** One message in the browser: bubble + attachments, media, share, call and actions. */
export const MessageRow = memo(function MessageRow({
  item,
  timeZone,
  highlight,
  showThread,
  compact,
  active,
  onOpenMedia,
  onContext,
}: MessageRowProps) {
  const isMe = item.sender.is_owner;
  const href = safeHref(item.share?.link);
  const lines = [
    ...attachmentLabels(item),
    shareLabel(item),
    callLabel(item),
    systemLabel(item),
  ].filter((s): s is string => Boolean(s));
  const media = item.attachments.media ?? [];
  const caption = `${item.sender.name} · ${new Date(item.ts).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
    ...(timeZone ? { timeZone } : {}),
  })}`;

  const attachments =
    lines.length || media.length || item.share?.text ? (
      <div className="flex flex-col gap-1">
        {lines.map((l, i) => (
          <span key={i}>{l}</span>
        ))}
        {item.share?.text && <span className="italic">“{item.share.text}”</span>}
        {href && (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="break-all underline"
          >
            {href}
          </a>
        )}
        {media.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {media.map((m) => (
              <MediaThumb key={m.media_id} media={m} caption={caption} onOpen={onOpenMedia} />
            ))}
          </div>
        )}
      </div>
    ) : undefined;

  return (
    <div
      className={cn(
        "group flex flex-col gap-0.5 rounded-lg px-2 py-1",
        active && "bg-yellow-100/70 ring-2 ring-yellow-400 dark:bg-yellow-900/30",
      )}
      data-message-id={item.message_id}
    >
      {showThread && !compact && (
        <span className={cn("px-9 text-[11px] text-muted", isMe && "text-right")}>
          in {item.thread_title}
        </span>
      )}
      <div className={cn("flex items-end gap-1", isMe ? "flex-row-reverse" : "flex-row")}>
        <MessageBubble
          className="min-w-0 flex-1"
          text={item.type === "unsent" ? null : item.text}
          unsent={item.type === "unsent"}
          senderName={item.sender.name}
          senderId={item.sender.person_id}
          senderColorIndex={item.sender.color_index}
          timestamp={item.ts}
          timeZone={timeZone}
          isMe={isMe}
          compact={compact}
          highlight={highlight}
          reactions={(item.reactions ?? []).map((r) => ({ emoji: r.emoji, actor: r.actor.name }))}
          attachments={attachments}
        />
        {onContext && (
          <button
            type="button"
            className="btn btn-sm shrink-0 opacity-0 transition-opacity focus:opacity-100 group-hover:opacity-100"
            onClick={() => onContext(item)}
            title="Jump to context: show this message in its chat"
          >
            Context
          </button>
        )}
      </div>
    </div>
  );
});

function MediaThumb({
  media,
  caption,
  onOpen,
}: {
  media: NonNullable<MessageItem["attachments"]["media"]>[number];
  caption: string;
  onOpen?: (m: MediaRef) => void;
}) {
  if (!media.exists)
    return (
      <span
        className="inline-flex h-16 w-16 items-center justify-center rounded border border-dashed border-current/40 text-[10px] opacity-70"
        title="File not in the export"
      >
        missing
      </span>
    );
  if (media.kind === "audio")
    return (
      <audio
        controls
        preload="none"
        src={mediaUrl(media.media_id)}
        className="h-8 w-56 max-w-full"
        aria-label="Voice note"
      />
    );
  if (media.kind === "video")
    return (
      <video
        controls
        preload="none"
        poster={thumbUrl(media.media_id, 256)}
        src={mediaUrl(media.media_id)}
        className="h-32 max-w-[14rem] rounded bg-black"
        aria-label="Video"
      />
    );
  return (
    <button
      type="button"
      className="overflow-hidden rounded focus-visible:ring-2"
      onClick={() => onOpen?.({ media_id: media.media_id, kind: media.kind, caption })}
      aria-label={`Open ${media.kind}`}
    >
      <img
        src={thumbUrl(media.media_id, 256)}
        alt=""
        loading="lazy"
        className="h-24 w-24 object-cover"
      />
    </button>
  );
}
