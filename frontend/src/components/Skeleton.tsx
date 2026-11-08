import type { CSSProperties } from "react";
import { cn } from "@/lib/utils";

/** Pulsing placeholder block. Size via className or `height`. */
export function Skeleton({
  className,
  height,
  style,
}: {
  className?: string;
  height?: number | string;
  style?: CSSProperties;
}) {
  return (
    <div
      aria-hidden="true"
      className={cn("animate-pulse rounded-md bg-surface-2", className)}
      style={{ height, ...style }}
    />
  );
}

/** Skeleton for a whole chart body, announced to screen readers as loading. */
export function ChartSkeleton({ height = 320 }: { height?: number | string }) {
  return (
    <div role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">Loading…</span>
      <Skeleton height={height} className="w-full" />
    </div>
  );
}
