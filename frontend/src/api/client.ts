import createClient from "openapi-fetch";
import { ApiError, toApiError } from "@/api/errors";
import { trackDatasetVersion } from "@/api/queryClient";
import type { paths } from "@/api/schema";

/** Typed openapi-fetch client. Paths include the `/api` prefix (e.g. "/api/overview"). */
export const client = createClient<paths>({ baseUrl: "" });

export type QueryValue =
  string | number | boolean | null | undefined | ReadonlyArray<string | number>;
export type QueryParams = Record<string, QueryValue>;
export type PathParams = Record<string, string | number>;

export interface RequestParams {
  /** Values for `{placeholders}` in the path, e.g. `{ id: "t1" }` for "/api/chats/{id}". */
  path?: PathParams;
  query?: QueryParams;
}

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

interface UntypedResult {
  data?: unknown;
  error?: unknown;
  response: Response;
}
type UntypedMethod = (path: string, init: Record<string, unknown>) => Promise<UntypedResult>;
const untyped = client as unknown as Record<HttpMethod, UntypedMethod>;

/** Drop null/undefined/"" values and join arrays with commas (backend convention). */
export function cleanQuery(q: QueryParams | undefined): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {};
  for (const [k, v] of Object.entries(q ?? {})) {
    if (v == null || v === "") continue;
    if (Array.isArray(v)) {
      if (v.length) out[k] = v.join(",");
    } else {
      out[k] = v as string | number | boolean;
    }
  }
  return out;
}

export interface ApiFetchOptions extends RequestParams {
  body?: unknown;
  signal?: AbortSignal;
}

/**
 * Imperative request helper (mutations, one-off calls). Returns the parsed JSON body,
 * throws `ApiError` on non-2xx or network failure. Does NOT add global filters.
 */
export async function apiFetch<T = unknown>(
  method: HttpMethod,
  path: string,
  opts: ApiFetchOptions = {},
): Promise<T> {
  let result: UntypedResult;
  try {
    result = await untyped[method](path, {
      params: { path: opts.path, query: cleanQuery(opts.query) },
      ...(opts.body !== undefined ? { body: opts.body } : {}),
      signal: opts.signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "network", e instanceof Error ? e.message : "Network error");
  }
  if (!result.response.ok) throw toApiError(result.response.status, result.error);
  trackDatasetVersion(result.data);
  return result.data as T;
}
