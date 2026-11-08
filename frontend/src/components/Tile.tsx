import type { ReactNode } from "react";
import { Skeleton } from "@/components/Skeleton";
import { useT } from "@/i18n";
import { useOpenMessages, type MessageQuery } from "@/lib/drilldown";
import { formatValue, type ValueFormat } from "@/lib/format";
import { cn } from "@/lib/utils";

export type TileFormat = ValueFormat;

export interface TileProps {
  label: ReactNode;
  value: number | string | null | undefined;
  /** How to format numeric values. Default "number". percent expects 0..1, duration seconds. */
  format?: TileFormat;
  /** Secondary line (e.g. "with Jane · Mar 5, 2021"). */
  hint?: ReactNode;
  /** Clicking the tile opens the Message Browser with this query (§5.1). */
  query?: MessageQuery;
  /** Custom click handler (used instead of `query`). */
  onClick?: () => void;
  loading?: boolean;
  icon?: ReactNode;
  className?: string;
}

/** Headline number. Clickable when `query` or `onClick` is given. */
export function Tile({
  label,
  value,
  format,
  hint,
  query,
  onClick,
  loading,
  icon,
  className,
}: TileProps) {
  const t = useT();
  const open = useOpenMessages();
  const handler = onClick ?? (query ? () => open(query) : undefined);
  const content = (
    <>
      <span className="flex items-center gap-1.5 text-xs font-medium text-muted">
        {icon}
        {label}
      </span>
      {loading ? (
        <Skeleton className="mt-1 h-7 w-24" />
      ) : (
        <span className="text-2xl font-semibold tabular-nums leading-tight">
          {formatValue(value, format)}
        </span>
      )}
      {hint && !loading && <span className="truncate text-xs text-muted">{hint}</span>}
    </>
  );
  const cls = cn("card flex min-w-0 flex-col gap-0.5 p-3 text-left", className);
  if (handler && !loading) {
    return (
      <button
        type="button"
        onClick={handler}
        className={cn(cls, "transition-colors hover:border-accent/60 hover:bg-surface-2")}
        title={t("tile.open")}
      >
        {content}
      </button>
    );
  }
  return <div className={cls}>{content}</div>;
}

/** Responsive grid of tiles (1 column on phones → up to 6 on wide screens). */
export function TileGrid({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6",
        className,
      )}
    >
      {children}
    </div>
  );
}
