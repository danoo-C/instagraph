import { createContext, useContext } from "react";
import { useApi } from "@/api/useApi";
import { personColor } from "@/lib/colors";
import type { ChatHeader, PersonRef } from "@/features/chat/types";

/** Value exposed by the chat detail page to all tabs. */
export interface ChatContextValue {
  /** Chat id as in the URL. */
  chatId: string;
  /** Numeric thread id (NaN if the URL is invalid). */
  threadId: number;
  /** Header data, undefined while loading. */
  header: ChatHeader | undefined;
  /** Participants (owner first), [] while loading. */
  participants: PersonRef[];
  isGroup: boolean | undefined;
  /** Owner person id if the owner is a participant. */
  ownerId: number | undefined;
  /** Look up a participant by id. */
  personById: (id: number | null | undefined) => PersonRef | undefined;
  /** Stable color of a person (by id, falls back to hash). */
  colorOf: (id: number | null | undefined) => string;
}

export const ChatContext = createContext<ChatContextValue | null>(null);

/** Chat header + participants. Must be used inside the chat detail page; returns null outside. */
export function useChatContext(): ChatContextValue | null {
  return useContext(ChatContext);
}

/** Parse a URL chat id into a thread id (NaN when invalid). */
export function parseChatId(chatId: string | number): number {
  if (typeof chatId === "number") return chatId;
  return /^\d+$/.test(chatId) ? Number(chatId) : NaN;
}

/** GET /api/chats/{id} (header). Shares the cache with the chat detail page. */
export function useChatHeader(chatId: string | number) {
  const id = parseChatId(chatId);
  return useApi("/api/chats/{chat_id}", { path: { chat_id: id } }, { enabled: !Number.isNaN(id) });
}

/** Build the context value from header data (pure; exported for tests). */
export function buildChatContext(chatId: string, header: ChatHeader | undefined): ChatContextValue {
  const participants = header?.participants ?? [];
  const byId = new Map(participants.map((p) => [p.person_id, p]));
  return {
    chatId,
    threadId: parseChatId(chatId),
    header,
    participants,
    isGroup: header ? header.is_group : undefined,
    ownerId: participants.find((p) => p.is_owner)?.person_id,
    personById: (id) => (id == null ? undefined : byId.get(id)),
    colorOf: (id) => {
      if (id == null) return personColor(null);
      const p = byId.get(id);
      return personColor(p ? { id, color_index: p.color_index } : String(id));
    },
  };
}
