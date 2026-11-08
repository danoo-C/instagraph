import type { ReactNode } from "react";
import { PersonBadge } from "@/components/PersonBadge";
import { useDrill } from "@/lib/drilldown";
import type { ApiMessageQuery, MessageRef } from "@/features/chat/types";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useFilters } from "@/state/filters";

/** A single message rendered as a quote (text only, never HTML). Click opens it in the browser. */
export function MessageQuote({
  message,
  drilldown,
  label,
}: {
  message: MessageRef | null | undefined;
  drilldown?: ApiMessageQuery | null;
  label?: ReactNode;
}) {
  const open = useDrill();
  const { filters } = useFilters();
  if (!message) return <p className="text-sm text-muted">—</p>;
  const q = drilldown ?? { message_ids: [message.message_id] };
  return (
    <button
      type="button"
      onClick={() => open(q)}
      className="flex w-full min-w-0 flex-col gap-1 rounded-md border border-border p-3 text-left hover:border-accent/60 hover:bg-surface-2"
      title="Open in Message Browser"
    >
      {label && <span className="text-xs font-medium text-muted">{label}</span>}
      <span className="flex items-center gap-2 text-xs text-muted">
        <PersonBadge
          name={message.sender.name}
          id={message.sender.person_id}
          color_index={message.sender.color_index}
          size="xs"
          showName
        />
        <span>·</span>
        <time dateTime={message.ts}>{formatDateTime(message.ts, filters.tz)}</time>
      </span>
      <blockquote className="whitespace-pre-wrap break-words border-l-2 border-border pl-2 text-sm">
        {message.text ?? <span className="italic text-muted">[{message.type}]</span>}
      </blockquote>
    </button>
  );
}

/** A card with a title for non-chart content (quote cards, lists). */
export function InfoCard({
  title,
  children,
  className,
}: {
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("card flex min-w-0 flex-col gap-3 p-4", className)} aria-label={title}>
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}
