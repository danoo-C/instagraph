import { useCallback } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import type { components } from "@/api/schema";
import { FILTER_KEYS } from "@/state/filters";

/** Backend `MessageQuery` — the `drilldown` objects in API responses (nulls, arrays). */
export type ApiMessageQuery = components["schemas"]["MessageQuery"];

type QueryValue = string | number | boolean | readonly (string | number)[] | null | undefined;

/**
 * Query for the Message Browser (/search, §5.9). Every count in the app should open
 * the messages behind it (§5.1). Keys become URL params on /search.
 *
 * Accepts both spellings: the short frontend names (`thread, sender, type, q, mode`) and
 * the backend `MessageQuery` names (`thread_id, sender_ids, types, text, text_mode, hour, …`),
 * so a backend `drilldown` can be passed as-is. Nulls/empty values are dropped, arrays are
 * comma-joined, and keys are written in the canonical backend spelling.
 * `start`/`end` override the global date range (e.g. a single busiest day).
 */
export interface MessageQuery {
  thread?: string | number;
  sender?: string | number;
  start?: string | null;
  end?: string | null;
  /** Message type, e.g. "text", "photo", "video", "audio", "share", "call", "system". */
  type?: string;
  has_reactions?: boolean | null;
  emoji?: string | null;
  /** Text search. */
  q?: string;
  mode?: "plain" | "word" | "regex";
  case_sensitive?: boolean | null;
  /** Any backend filter (`thread_id`, `types`, `hour`, `message_ids`, …) or extra param. */
  [key: string]: QueryValue;
}

/** Frontend alias → canonical backend `MessageQuery` key (what `/search` writes). */
const CANONICAL: Record<string, string> = {
  thread: "thread_id",
  sender: "sender_id",
  type: "types",
  q: "text",
  mode: "text_mode",
};

function encodeValue(v: QueryValue): string | null {
  if (v == null || v === "") return null;
  if (Array.isArray(v)) return v.length ? v.join(",") : null;
  return String(v);
}

/**
 * Normalise a (backend or frontend) query into URL-ready string values with canonical keys.
 * Later keys win; an empty value removes the key.
 */
export function normalizeMessageQuery(
  ...queries: (MessageQuery | ApiMessageQuery | null | undefined)[]
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const q of queries) {
    if (!q) continue;
    for (const [k, v] of Object.entries(q as MessageQuery)) {
      const key = CANONICAL[k] ?? k;
      const s = encodeValue(v);
      if (s == null) delete out[key];
      else out[key] = s;
    }
  }
  return out;
}

/**
 * Backend drilldown (+ optional overrides) → `MessageQuery` (canonical keys, string values).
 * Always returns an object; an empty object means "all messages under the global filters".
 */
export function toMessageQuery(
  q: ApiMessageQuery | MessageQuery | null | undefined,
  extra?: MessageQuery,
): MessageQuery {
  return normalizeMessageQuery(q, extra);
}

/** Like `toMessageQuery`, but `undefined` when there is no drilldown (e.g. for `Tile query=`). */
export function drilldownQuery(
  q: ApiMessageQuery | MessageQuery | null | undefined,
): MessageQuery | undefined {
  return q ? toMessageQuery(q) : undefined;
}

/**
 * Build the /search URL for a query. Global filters from `current` are kept
 * (except those the query overrides); other page-specific params are dropped.
 */
export function messagesHref(
  query: MessageQuery | ApiMessageQuery,
  current?: URLSearchParams,
): string {
  const out = new URLSearchParams();
  if (current) {
    for (const k of FILTER_KEYS) {
      const v = current.get(k);
      if (v != null) out.set(k, v);
    }
  }
  for (const [k, v] of Object.entries(query as MessageQuery)) {
    const key = CANONICAL[k] ?? k;
    const s = encodeValue(v);
    if (s == null) out.delete(key);
    else out.set(key, s);
  }
  const qs = out.toString();
  return qs ? `/search?${qs}` : "/search";
}

/** Returns `open(query)` that navigates to the Message Browser. */
export function useOpenMessages(): (query: MessageQuery | ApiMessageQuery) => void {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  return useCallback((query) => navigate(messagesHref(query, params)), [navigate, params]);
}

/**
 * `drill(apiQuery, extra?)` opens the Message Browser for a backend drilldown
 * (merged with `extra`, later wins). A null drilldown without extras is a no-op.
 */
export function useDrill(): (
  q: ApiMessageQuery | MessageQuery | null | undefined,
  extra?: MessageQuery,
) => void {
  const open = useOpenMessages();
  return useCallback(
    (q, extra) => {
      if (q || extra) open(toMessageQuery(q, extra));
    },
    [open],
  );
}

/** Open the Message Browser with the context view of one message (thread around it). */
export function useOpenContext(): (messageId: number, threadId?: number) => void {
  const open = useOpenMessages();
  return useCallback(
    (messageId, threadId) =>
      open({ ...(threadId != null ? { thread_id: threadId } : {}), context: messageId }),
    [open],
  );
}
