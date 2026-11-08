import type { ReactNode } from "react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

export function EmptyState({
  title,
  description,
  action,
  className,
}: {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  const t = useT();
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border p-8 text-center",
        className,
      )}
    >
      <p className="font-medium">{title ?? t("empty.title")}</p>
      {description && <p className="max-w-prose text-sm text-muted">{description}</p>}
      {action}
    </div>
  );
}
