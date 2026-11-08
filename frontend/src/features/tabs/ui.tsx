import type { ReactNode } from "react";
import { ErrorCard } from "@/components/ErrorCard";
import { ChartSkeleton } from "@/components/Skeleton";

/** Whole-tab loading / error wrapper for a single-endpoint tab. */
export function TabState({
  query,
  children,
}: {
  query: { isLoading: boolean; error: unknown; refetch: () => unknown; data?: unknown };
  children: () => ReactNode;
}) {
  if (query.isLoading)
    return (
      <div className="flex flex-col gap-4">
        <ChartSkeleton height={96} />
        <div className="card-grid">
          <ChartSkeleton />
          <ChartSkeleton />
        </div>
      </div>
    );
  if (query.error && !query.data)
    return <ErrorCard error={query.error} onRetry={() => void query.refetch()} />;
  if (!query.data) return null;
  return <>{children()}</>;
}

/** Section heading inside a tab. */
export function SectionTitle({ children }: { children: ReactNode }) {
  return <h2 className="mt-2 text-base font-semibold">{children}</h2>;
}
