import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export interface SegmentedOption<T extends string> {
  value: T;
  label: ReactNode;
}

export interface SegmentedProps<T extends string> {
  value: T;
  options: readonly SegmentedOption<T>[];
  onChange: (v: T) => void;
  /** Accessible name of the group. */
  label: string;
  className?: string;
}

/** Small segmented toggle for chart-card headers (day/week/month, metric, view…). */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  className,
}: SegmentedProps<T>) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn("inline-flex rounded-md border border-border p-0.5", className)}
    >
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "rounded px-2 py-0.5 text-xs transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent",
            value === o.value ? "bg-accent text-accent-fg" : "text-muted hover:bg-surface-2",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
