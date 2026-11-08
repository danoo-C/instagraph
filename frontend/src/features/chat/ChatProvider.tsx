import { useMemo, type ReactNode } from "react";
import { ChatContext, buildChatContext } from "@/features/chat/context";
import type { ChatHeader } from "@/features/chat/types";

/** Provides header data (participants, colors, owner) to all chat tabs. */
export function ChatProvider({
  chatId,
  header,
  children,
}: {
  chatId: string;
  header: ChatHeader | undefined;
  children: ReactNode;
}) {
  const value = useMemo(() => buildChatContext(chatId, header), [chatId, header]);
  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}
