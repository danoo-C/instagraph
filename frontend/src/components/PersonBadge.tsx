import { personColor, readableTextColor, type PersonColorSource } from "@/lib/colors";
import { cn, initials } from "@/lib/utils";

export interface PersonBadgeProps {
  name: string;
  id?: string | number | null;
  color_index?: number | null;
  size?: "xs" | "sm" | "md" | "lg";
  /** Show the name next to the avatar. */
  showName?: boolean;
  className?: string;
}

const SIZES = {
  xs: "h-5 w-5 text-[9px]",
  sm: "h-6 w-6 text-[10px]",
  md: "h-8 w-8 text-xs",
  lg: "h-12 w-12 text-base",
} as const;

/** Colored-initials avatar; color is stable per person across the app. */
export function PersonBadge({
  name,
  id,
  color_index,
  size = "sm",
  showName,
  className,
}: PersonBadgeProps) {
  const source: PersonColorSource = { id: id ?? name, color_index };
  const bg = personColor(source);
  return (
    <span className={cn("inline-flex min-w-0 items-center gap-1.5", className)}>
      <span
        className={cn(
          "inline-flex shrink-0 select-none items-center justify-center rounded-full font-semibold",
          SIZES[size],
        )}
        style={{ backgroundColor: bg, color: readableTextColor(bg) }}
        aria-hidden={showName ? true : undefined}
        role={showName ? undefined : "img"}
        aria-label={showName ? undefined : name}
        title={name}
      >
        {initials(name)}
      </span>
      {showName && <span className="truncate">{name}</span>}
    </span>
  );
}
