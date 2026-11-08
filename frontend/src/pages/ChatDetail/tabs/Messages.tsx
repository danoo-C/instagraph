import { MessageBrowser } from "@/features/messages/MessageBrowser";
import type { ChatTabProps } from "@/pages/ChatDetail/tabs";

/** Messages tab: the Message Browser scoped to this chat (thread fixed). */
export default function MessagesTab({ chatId }: ChatTabProps) {
  return <MessageBrowser fixedThreadId={Number(chatId)} />;
}
