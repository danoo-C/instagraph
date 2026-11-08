import { QueryClient } from "@tanstack/react-query";
import { isApiError } from "@/api/errors";

/** App-wide QueryClient singleton (also used for dataset_version invalidation). */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60_000,
      gcTime: 30 * 60_000,
      refetchOnWindowFocus: false,
      retry: (count, err) => {
        if (isApiError(err) && err.status >= 400 && err.status < 500) return false;
        return count < 2;
      },
    },
  },
});

let lastDatasetVersion: string | null = null;

/**
 * Call with every API response body. When `dataset_version` changes (re-import or a
 * settings change that re-derives data), every cached query is invalidated.
 */
export function trackDatasetVersion(body: unknown): void {
  if (!body || typeof body !== "object" || !("dataset_version" in body)) return;
  const v = (body as { dataset_version: unknown }).dataset_version;
  if (v == null) return;
  const version = String(v);
  if (lastDatasetVersion !== null && lastDatasetVersion !== version) {
    lastDatasetVersion = version;
    void queryClient.invalidateQueries();
    return;
  }
  lastDatasetVersion = version;
}

/** For tests / "Clear all data". */
export function resetDatasetVersion(): void {
  lastDatasetVersion = null;
}
