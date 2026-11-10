import { apiFetch } from "@/api/client";
import { toApiError } from "@/api/errors";
import { queryClient } from "@/api/queryClient";
import type { components } from "@/api/schema";
import { useApi } from "@/api/useApi";

type S = components["schemas"];
export type AiStatus = S["AiStatusResponse"];
export type AiProviderInfo = S["AiProviderInfo"];
export type AiConversation = S["AiConversation"];
export type AiConversationSummary = S["AiConversationSummary"];
export type AiTurn = S["AiTurn"];
export type AiToolActivity = S["AiToolActivity"];
export type AiUsage = S["AiUsage"];
export type AiLastRequest = S["AiLastRequest"];
export type AiConversationCreate = S["AiConversationCreate"];
export type AiConversationPatch = S["AiConversationPatch"];
export type AiKeyInfo = S["AiKeyInfo"];
export type AiKeyTest = S["AiKeyTest"];
type ChatListResponse = S["ChatListResponse"];

/** One event of an answer stream (`POST /api/ai/conversations/{id}/messages`). */
export type AiStreamEvent =
  | { type: "text"; text: string }
  | { type: "tool_start"; id: string; name: string; args: Record<string, unknown> }
  | {
      type: "tool_end";
      id: string;
      name: string;
      summary: string;
      is_error: boolean;
      truncated: boolean;
    }
  | ({ type: "usage" } & AiUsage)
  | { type: "error"; code: string; message: string }
  | { type: "done"; status: AiTurn["status"] };

// ---------------------------------------------------------------- queries

export function useAiStatus() {
  return useApi("/api/ai/status", undefined, { filters: false, keepPrevious: false });
}

/** Only call with `enabled` true: AI endpoints answer 409 while the feature is off. */
export function useAiConversations(enabled: boolean) {
  return useApi("/api/ai/conversations", undefined, { filters: false, enabled });
}

export function useAiConversation(id: string | undefined, enabled: boolean) {
  return useApi(
    "/api/ai/conversations/{conv_id}",
    { path: { conv_id: id ?? "" } },
    { filters: false, enabled: enabled && !!id, keepPrevious: false },
  );
}

export function refreshAi(): Promise<void> {
  return queryClient.invalidateQueries({
    predicate: (q) => typeof q.queryKey[1] === "string" && q.queryKey[1].startsWith("/api/ai/"),
  });
}

/** Every chat (all categories), most messages first, for the allowed-chats picker. */
export function useAllChats() {
  return useApi<ChatListResponse>(
    "/api/chats",
    {
      query: {
        limit: 500,
        sort: "messages",
        include_groups: true,
        include_requests: true,
        include_archived: true,
      },
    },
    { filters: false },
  );
}

// ---------------------------------------------------------------- mutations

export const createConversation = (body: AiConversationCreate) =>
  apiFetch<AiConversation>("POST", "/api/ai/conversations", { body });

export const patchConversation = (id: string, body: AiConversationPatch) =>
  apiFetch<AiConversation>("PATCH", "/api/ai/conversations/{conv_id}", {
    path: { conv_id: id },
    body,
  });

export const deleteConversation = (id: string) =>
  apiFetch("DELETE", "/api/ai/conversations/{conv_id}", { path: { conv_id: id } });

export const deleteAllConversations = () => apiFetch("DELETE", "/api/ai/conversations");

export const stopAnswer = (id: string) =>
  apiFetch("POST", "/api/ai/conversations/{conv_id}/stop", { path: { conv_id: id } });

export const saveApiKey = (key: string) =>
  apiFetch<AiKeyInfo>("PUT", "/api/ai/key", { body: { key } });

export const removeApiKey = () => apiFetch<AiKeyInfo>("DELETE", "/api/ai/key");

/** Test the given key, or the stored one when `key` is omitted (a free API call). */
export const testApiKey = (key?: string) =>
  apiFetch<AiKeyTest>("POST", "/api/ai/key/test", key ? { body: { key } } : {});

export const fetchLastRequest = (id: string) =>
  apiFetch<AiLastRequest>("GET", "/api/ai/conversations/{conv_id}/last-request", {
    path: { conv_id: id },
  });

// ---------------------------------------------------------------- streaming

/**
 * Split SSE text into complete events. Returns the parsed `data` JSON of each complete
 * event and the unfinished remainder (keep it for the next chunk). Handles `\n` and
 * `\r\n` line endings; ignores comments (pings) and events without data.
 */
export function parseSse(buffer: string): { events: AiStreamEvent[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const blocks = normalized.split("\n\n");
  const rest = blocks.pop() ?? "";
  const events: AiStreamEvent[] = [];
  for (const block of blocks) {
    const data = block
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, ""))
      .join("\n");
    if (!data) continue;
    try {
      events.push(JSON.parse(data) as AiStreamEvent);
    } catch {
      // not JSON: ignore
    }
  }
  return { events, rest };
}

/**
 * Send a message and call `onEvent` for every streamed event. Resolves when the stream
 * ends; throws `ApiError` when the request is refused before the answer starts.
 */
export async function streamMessage(
  convId: string,
  text: string,
  onEvent: (e: AiStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/ai/conversations/${encodeURIComponent(convId)}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ text }),
    signal,
  });
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null);
    throw toApiError(res.status, body);
  }
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSse(buffer);
    buffer = parsed.rest;
    parsed.events.forEach(onEvent);
  }
  parseSse(buffer + "\n\n").events.forEach(onEvent);
}

/** "12.4k" style token counts. */
export function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 100_000) return `${(n / 1000).toFixed(1)}k`;
  return `${Math.round(n / 1000)}k`;
}
