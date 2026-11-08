import { useEffect } from "react";
import {
  keepPreviousData,
  useInfiniteQuery,
  useQuery,
  type InfiniteData,
  type UseInfiniteQueryResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";
import { apiFetch, type QueryParams, type RequestParams } from "@/api/client";
import type { ApiError } from "@/api/errors";
import type { paths } from "@/api/schema";
import { useFilters } from "@/state/filters";

// ---------------------------------------------------------------- type helpers (generated schema)

/** Paths that have a GET operation in the generated schema. */
export type GetPath = {
  [P in keyof paths]: paths[P] extends { get: { responses: unknown } } ? P : never;
}[keyof paths] &
  string;

type GetOp<P extends GetPath> = paths[P] extends { get: infer Op } ? Op : never;

/** JSON body of the 200 response of `GET P`. */
export type ApiResponse<P extends GetPath> =
  GetOp<P> extends { responses: { 200: { content: { "application/json": infer R } } } }
    ? R
    : unknown;

/** Params for `GET P`. Query params are partial because global filters are merged in. */
export type ApiParams<P extends GetPath> = {
  path?: GetOp<P> extends { parameters: { path: infer X } } ? X : never;
  query?: GetOp<P> extends { parameters: { query?: infer Q } }
    ? Partial<NonNullable<Q>>
    : QueryParams;
};

// ---------------------------------------------------------------- options

export interface UseApiOptions {
  /** Merge global filters (§8.2) into the query. Default `true`. */
  filters?: boolean;
  enabled?: boolean;
  staleTime?: number;
  /** Keep the previous data visible while refetching after a filter change. Default `true`. */
  keepPrevious?: boolean;
  refetchInterval?: number | false;
}

/** Hook: on 409 (no data imported) redirect to /import. */
export function useRedirectOnNoData(error: ApiError | null | undefined): void {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const noData = error?.status === 409;
  useEffect(() => {
    if (noData && !pathname.startsWith("/import")) navigate("/import", { replace: true });
  }, [noData, pathname, navigate]);
}

function useMergedQuery(query: QueryParams | undefined, withFilters: boolean): QueryParams {
  const { toQuery } = useFilters();
  return withFilters ? { ...toQuery(), ...query } : { ...query };
}

/**
 * GET an API endpoint through TanStack Query.
 *
 * - Global filters are merged into the query (override per call via `params.query`).
 * - Errors are `ApiError` (`{status, code, message}`); 409 redirects to /import.
 * - `dataset_version` changes invalidate all cached queries.
 *
 * Typed when the path exists in the generated schema; otherwise pass the type:
 * `useApi<OverviewTiles>("/api/overview")`.
 */
export function useApi<P extends GetPath>(
  path: P,
  params?: ApiParams<P>,
  options?: UseApiOptions,
): UseQueryResult<ApiResponse<P>, ApiError>;
export function useApi<T = unknown>(
  path: string,
  params?: RequestParams,
  options?: UseApiOptions,
): UseQueryResult<T, ApiError>;
export function useApi(
  path: string,
  params?: RequestParams,
  options: UseApiOptions = {},
): UseQueryResult<unknown, ApiError> {
  const query = useMergedQuery(params?.query, options.filters ?? true);
  const pathParams = params?.path;
  const result = useQuery<unknown, ApiError>({
    queryKey: apiQueryKey(path, pathParams, query),
    queryFn: ({ signal }) => apiFetch("GET", path, { path: pathParams, query, signal }),
    enabled: options.enabled ?? true,
    staleTime: options.staleTime,
    refetchInterval: options.refetchInterval,
    placeholderData: options.keepPrevious === false ? undefined : keepPreviousData,
  });
  useRedirectOnNoData(result.error);
  return result;
}

/** Query key used by useApi — use it for targeted invalidation: `["api", "/api/settings"]`. */
export function apiQueryKey(path: string, pathParams?: RequestParams["path"], query?: QueryParams) {
  return ["api", path, pathParams ?? null, query ?? {}] as const;
}

export interface UseApiInfiniteOptions<T> extends UseApiOptions {
  /** Extract the next cursor from a page; return null/undefined when there are no more pages. */
  getNextCursor: (lastPage: T) => string | number | null | undefined;
  /** Query param name for the cursor. Default "cursor". */
  cursorParam?: string;
}

/** Cursor-paginated GET (Message Browser, chat list). Pages are in `data.pages`. */
export function useApiInfinite<T>(
  path: string,
  params: RequestParams | undefined,
  options: UseApiInfiniteOptions<T>,
): UseInfiniteQueryResult<InfiniteData<T, string | number | null>, ApiError> {
  const query = useMergedQuery(params?.query, options.filters ?? true);
  const pathParams = params?.path;
  const cursorParam = options.cursorParam ?? "cursor";
  const result = useInfiniteQuery<
    T,
    ApiError,
    InfiniteData<T, string | number | null>,
    ReturnType<typeof apiQueryKey>,
    string | number | null
  >({
    queryKey: apiQueryKey(path, pathParams, { ...query, __infinite: true }),
    initialPageParam: null,
    queryFn: ({ signal, pageParam }) =>
      apiFetch<T>("GET", path, {
        path: pathParams,
        query: { ...query, [cursorParam]: pageParam },
        signal,
      }),
    getNextPageParam: (last) => options.getNextCursor(last) ?? undefined,
    enabled: options.enabled ?? true,
    staleTime: options.staleTime,
  });
  useRedirectOnNoData(result.error);
  return result;
}
