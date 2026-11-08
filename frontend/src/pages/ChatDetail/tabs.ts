import { lazy, type ComponentType } from "react";
import type { TranslationKey } from "@/i18n";

/** Props every chat tab receives. Each tab calls its own endpoint `/api/chats/{id}/{tab}`. */
export interface ChatTabProps {
  chatId: string;
}

export interface ChatTabDef {
  /** URL segment and backend tab name. */
  id: string;
  label: TranslationKey;
  /** Lazy component: its chunk is only loaded when the tab is opened. */
  component: ComponentType<ChatTabProps>;
  /** Only shown for group chats. */
  groupOnly?: boolean;
}

/** Chat detail tabs (§5.6), in display order. */
export const CHAT_TABS: ChatTabDef[] = [
  {
    id: "summary",
    label: "chat.tab.summary",
    component: lazy(() => import("@/pages/ChatDetail/tabs/Summary")),
  },
  {
    id: "timeline",
    label: "chat.tab.timeline",
    component: lazy(() => import("@/pages/ChatDetail/tabs/Timeline")),
  },
  {
    id: "when",
    label: "chat.tab.when",
    component: lazy(() => import("@/pages/ChatDetail/tabs/When")),
  },
  {
    id: "dynamics",
    label: "chat.tab.dynamics",
    component: lazy(() => import("@/pages/ChatDetail/tabs/Dynamics")),
  },
  {
    id: "words",
    label: "chat.tab.words",
    component: lazy(() => import("@/pages/ChatDetail/tabs/Words")),
  },
  {
    id: "emojis",
    label: "chat.tab.emojis",
    component: lazy(() => import("@/pages/ChatDetail/tabs/Emojis")),
  },
  {
    id: "reactions",
    label: "chat.tab.reactions",
    component: lazy(() => import("@/pages/ChatDetail/tabs/Reactions")),
  },
  {
    id: "media",
    label: "chat.tab.media",
    component: lazy(() => import("@/pages/ChatDetail/tabs/Media")),
  },
  {
    id: "calls",
    label: "chat.tab.calls",
    component: lazy(() => import("@/pages/ChatDetail/tabs/Calls")),
  },
  {
    id: "group",
    label: "chat.tab.group",
    component: lazy(() => import("@/pages/ChatDetail/tabs/Group")),
    groupOnly: true,
  },
  {
    id: "awards",
    label: "chat.tab.awards",
    component: lazy(() => import("@/pages/ChatDetail/tabs/Awards")),
  },
  {
    id: "messages",
    label: "chat.tab.messages",
    component: lazy(() => import("@/pages/ChatDetail/tabs/Messages")),
  },
];

export const DEFAULT_CHAT_TAB = "summary";

/** Tabs visible for a chat; group-only tabs are hidden once we know it's a 1:1 chat. */
export function visibleChatTabs(isGroup: boolean | undefined): ChatTabDef[] {
  return CHAT_TABS.filter((d) => !d.groupOnly || isGroup !== false);
}
