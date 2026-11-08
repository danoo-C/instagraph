import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Native `<select>` styled like `.input`. `size="sm"` fits chart-card headers. */
export function SelectInput({
  value,
  onChange,
  label,
  children,
  size = "md",
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  /** Accessible name. */
  label: string;
  children: ReactNode;
  size?: "sm" | "md";
  className?: string;
}) {
  return (
    <select
      aria-label={label}
      className={cn("input py-0", size === "sm" ? "h-7 text-xs" : "h-8 text-sm", className)}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {children}
    </select>
  );
}
