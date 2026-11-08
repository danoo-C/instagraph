import type { QueryParams } from "@/api/client";

/**
 * Message Browser query ⇄ URL params (§5.9).
 *
 * The URL is the source of truth. Drilldowns arrive as `/search?<params>` built by
 * `messagesHref()` from either the frontend `MessageQuery` (`thread, sender, type, q,
 * mode`) or a backend `MessageQuery` (`thread_id, sender_id, types, text, text_mode, …`).
 * Both spellings are accepted; the browser writes the canonical (backend) spelling.
 * `start`/`end` are the global date filters and are handled by `useFilters`.
 */

export const TEXT_MODES = ["plain", "word", "regex"] as const;
export type TextMode = (typeof TEXT_MODES)[number];
export type SortOrder = "asc" | "desc";

export const MESSAGE_TYPES = [
  "text",
  "photo",
  "video",
  "audio",
  "gif",
  "sticker",
  "share",
  "link",
  "call",
  "like",
  "unsent",
  "system",
  "unknown",
] as const;

export interface BrowserQuery {
  thread_ids: number[];
  sender_ids: number[];
  types: string[];
  has_reactions: boolean | null;
  emoji: string;
  text: string;
  text_mode: TextMode;
  case_sensitive: boolean;
  accent_insensitive: boolean;
  order: SortOrder;
  /** Message id whose thread context is open (dialog). */
  context: number | null;
  /** Start the result list at this message id (loads both directions from there). */
  at: number | null;
  /** Other backend filters passed through unchanged (hour, weekday, message_ids, …). */
  extra: Record<string, string>;
}

/** Backend filters that are passed through as-is (strings). */
export const EXTRA_KEYS = [
  "hour",
  "weekday",
  "month",
  "emoticon",
  "reaction_emoji",
  "reactor_id",
  "share_kind",
  "share_owner",
  "link_domain",
  "call_kind",
  "call_missed",
  "system_event",
  "session_id",
  "message_ids",
] as const;

/** Aliases (frontend MessageQuery spelling → canonical). */
const ALIASES: Record<string, string[]> = {
  thread_ids: ["thread", "thread_id", "thread_ids"],
  sender_ids: ["sender", "sender_id", "sender_ids"],
  types: ["type", "types"],
  text: ["q", "text"],
  text_mode: ["mode", "text_mode"],
};

/** Every URL param owned by the browser (removed/rewritten by `writeBrowserParams`). */
export const BROWSER_PARAM_KEYS: readonly string[] = [
  ...Object.values(ALIASES).flat(),
  "has_reactions",
  "emoji",
  "case_sensitive",
  "accent_insensitive",
  "order",
  "context",
  "at",
  ...EXTRA_KEYS,
];

export function emptyBrowserQuery(): BrowserQuery {
  return {
    thread_ids: [],
    sender_ids: [],
    types: [],
    has_reactions: null,
    emoji: "",
    text: "",
    text_mode: "plain",
    case_sensitive: false,
    accent_insensitive: false,
    order: "desc",
    context: null,
    at: null,
    extra: {},
  };
}

function splitList(values: string[]): string[] {
  const out: string[] = [];
  for (const v of values)
    for (const p of v.split(",")) {
      const s = p.trim();
      if (s && !out.includes(s)) out.push(s);
    }
  return out;
}

function intList(values: string[]): number[] {
  return splitList(values)
    .map(Number)
    .filter((n) => Number.isInteger(n) && n >= 0);
}

function parseBool(v: string | null): boolean | null {
  if (v === "true" || v === "1") return true;
  if (v === "false" || v === "0") return false;
  return null;
}

function parseId(v: string | null): number | null {
  if (!v || !/^\d+$/.test(v)) return null;
  return Number(v);
}

function allOf(params: URLSearchParams, keys: string[]): string[] {
  return keys.flatMap((k) => params.getAll(k));
}

/** URL → BrowserQuery (accepts both frontend and backend spellings). */
export function parseBrowserParams(params: URLSearchParams): BrowserQuery {
  const q = emptyBrowserQuery();
  q.thread_ids = intList(allOf(params, ALIASES.thread_ids!));
  q.sender_ids = intList(allOf(params, ALIASES.sender_ids!));
  q.types = splitList(allOf(params, ALIASES.types!)).filter((t) =>
    (MESSAGE_TYPES as readonly string[]).includes(t),
  );
  q.has_reactions = parseBool(params.get("has_reactions"));
  q.emoji = params.get("emoji") ?? "";
  q.text = allOf(params, ALIASES.text!)[0] ?? "";
  const mode = allOf(params, ALIASES.text_mode!)[0];
  q.text_mode = (TEXT_MODES as readonly string[]).includes(mode ?? "")
    ? (mode as TextMode)
    : "plain";
  q.case_sensitive = parseBool(params.get("case_sensitive")) === true;
  q.accent_insensitive = parseBool(params.get("accent_insensitive")) === true;
  q.order = params.get("order") === "asc" ? "asc" : "desc";
  q.context = parseId(params.get("context"));
  q.at = parseId(params.get("at"));
  for (const k of EXTRA_KEYS) {
    const v = params.get(k);
    if (v != null && v !== "") q.extra[k] = v;
  }
  return q;
}

/**
 * BrowserQuery → URL params. Starts from `base` (keeps global filters and unrelated
 * params), removes every browser key/alias, then writes the canonical non-default values.
 */
export function writeBrowserParams(
  q: BrowserQuery,
  base: URLSearchParams = new URLSearchParams(),
): URLSearchParams {
  const out = new URLSearchParams(base);
  for (const k of BROWSER_PARAM_KEYS) out.delete(k);
  const list = (single: string, multi: string, ids: number[]) => {
    if (ids.length === 1) out.set(single, String(ids[0]));
    else if (ids.length > 1) out.set(multi, ids.join(","));
  };
  list("thread_id", "thread_ids", q.thread_ids);
  list("sender_id", "sender_ids", q.sender_ids);
  if (q.types.length) out.set("types", q.types.join(","));
  if (q.has_reactions != null) out.set("has_reactions", String(q.has_reactions));
  if (q.emoji) out.set("emoji", q.emoji);
  if (q.text) {
    out.set("text", q.text);
    if (q.text_mode !== "plain") out.set("text_mode", q.text_mode);
    if (q.case_sensitive) out.set("case_sensitive", "true");
    if (q.accent_insensitive) out.set("accent_insensitive", "true");
  }
  if (q.order !== "desc") out.set("order", q.order);
  if (q.context != null) out.set("context", String(q.context));
  if (q.at != null) out.set("at", String(q.at));
  for (const k of EXTRA_KEYS) {
    const v = q.extra[k];
    if (v) out.set(k, v);
  }
  return out;
}

/** BrowserQuery → backend query params for `/api/messages*` (global filters are merged separately). */
export function toApiQuery(q: BrowserQuery): QueryParams {
  const p: QueryParams = {};
  if (q.thread_ids.length === 1) p.thread_id = q.thread_ids[0];
  else if (q.thread_ids.length > 1) p.thread_ids = q.thread_ids;
  if (q.sender_ids.length === 1) p.sender_id = q.sender_ids[0];
  else if (q.sender_ids.length > 1) p.sender_ids = q.sender_ids;
  if (q.types.length) p.types = q.types;
  if (q.has_reactions != null) p.has_reactions = q.has_reactions;
  if (q.emoji) p.emoji = q.emoji;
  if (q.text) {
    p.text = q.text;
    p.text_mode = q.text_mode;
    if (q.case_sensitive) p.case_sensitive = true;
    if (q.accent_insensitive) p.accent_insensitive = true;
  }
  for (const [k, v] of Object.entries(q.extra)) p[k] = v;
  return p;
}

/** Number of active (non-default) browser filters, for the "Clear" button / badge. */
export function activeFilterCount(q: BrowserQuery, fixedThread = false): number {
  let n = 0;
  if (q.thread_ids.length && !fixedThread) n++;
  if (q.sender_ids.length) n++;
  if (q.types.length) n++;
  if (q.has_reactions != null) n++;
  if (q.emoji) n++;
  if (q.text) n++;
  return n + Object.keys(q.extra).length;
}

/** Human label for an extra filter chip. */
export function extraLabel(key: string, value: string): string {
  const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  switch (key) {
    case "hour":
      return `Hour ${value.padStart(2, "0")}:00`;
    case "weekday":
      return weekdays[Number(value) - 1] ?? `Weekday ${value}`;
    case "month":
      return new Intl.DateTimeFormat(undefined, { month: "long" }).format(
        new Date(2000, Number(value) - 1, 1),
      );
    case "message_ids": {
      const n = value.split(",").filter(Boolean).length;
      return n === 1 ? "1 specific message" : `${n} specific messages`;
    }
    case "call_missed":
      return value === "true" ? "Missed calls" : "Answered calls";
    default:
      return `${key.replace(/_/g, " ")}: ${value}`;
  }
}

// ---------------------------------------------------------------- cursors

/**
 * Cursor that positions a page so that message `id` is the first item shown.
 * Mirrors the backend's opaque cursor (`base64url("<ts_ms>:<message_id>")`); only the id
 * part is used for keyset paging and the cursor message itself is excluded.
 */
export function cursorAt(id: number, order: SortOrder): string {
  const pivot = order === "desc" ? id + 1 : id - 1;
  return encodeCursor(0, Math.max(pivot, 0));
}

export function encodeCursor(tsMs: number, id: number): string {
  return btoa(`${tsMs}:${id}`).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Escape a literal string for use in a regex search. */
export function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
