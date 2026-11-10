/**
 * Reports mode (docs/ai-assistant.md A7): ready-made prompts. The prompt is shown and
 * editable before sending, and a report is a normal conversation, so follow-ups work.
 */

export type ReportNeed = "chat" | "group" | "year" | "chats";

export interface ReportInput {
  chat?: { id: number; title: string };
  year?: number;
}

export interface ReportTemplate {
  id: string;
  title: string;
  description: string;
  /** What the user picks: one chat, one group chat, a year (+ chats), or chats. */
  need: ReportNeed;
  prompt: (input: ReportInput) => string;
  /** Conversation title. */
  label: (input: ReportInput) => string;
}

const chatRef = (i: ReportInput) => (i.chat ? `${i.chat.title} (chat_id ${i.chat.id})` : "…");

export const REPORTS: ReportTemplate[] = [
  {
    id: "chat-change",
    title: "How a chat changed over time",
    description: "Phases, turning points and what you talked about, with examples.",
    need: "chat",
    label: (i) => `How my chat with ${i.chat?.title ?? "…"} changed`,
    prompt: (i) =>
      `How did my chat with ${chatRef(i)} change over time? Look at the timeline, who starts ` +
      "conversations, reply times and what we talked about in different periods. Describe the " +
      "main phases and turning points, and quote a few example messages.",
  },
  {
    id: "effort",
    title: "Who puts in more effort",
    description: "Initiations, reply times, double texts and message length, side by side.",
    need: "chat",
    label: (i) => `Effort in my chat with ${i.chat?.title ?? "…"}`,
    prompt: (i) =>
      `Who puts more effort into keeping in touch in my chat with ${chatRef(i)}? Compare who ` +
      "starts conversations, reply times, double texts, message lengths and who has the last " +
      "word. Give a fair verdict backed by numbers.",
  },
  {
    id: "personality",
    title: "My texting personality",
    description: "When and how you text across the chats you choose.",
    need: "chats",
    label: () => "My texting personality",
    prompt: () =>
      "Describe my texting personality across the chats I shared: when I text, how fast I " +
      "reply, how long my messages are, my favourite words and emojis, and how I differ " +
      "between chats. Keep it fun but honest, and back it up with numbers.",
  },
  {
    id: "year",
    title: "Highlights of a year",
    description: "Busiest chats and days, how the year went, memorable moments.",
    need: "year",
    label: (i) => `Highlights of ${i.year ?? "…"}`,
    prompt: (i) =>
      `Give me the highlights of ${i.year ?? "…"} in the chats I shared: the busiest chats and ` +
      "days, how my messaging changed through the year, memorable moments and a few example " +
      "messages. Use the year recap data.",
  },
  {
    id: "group",
    title: "Friend group dynamics",
    description: "Who talks, who replies to whom, recurring topics in a group chat.",
    need: "group",
    label: (i) => `Group dynamics: ${i.chat?.title ?? "…"}`,
    prompt: (i) =>
      `Describe the group dynamics in the group chat ${chatRef(i)}: who talks most, who ` +
      "replies to whom, who starts conversations, recurring topics or inside jokes, and how " +
      "the group changed over time.",
  },
];

/** Allowed chats for a new report: the picked chat, or the chats chosen in the dialog. */
export function reportScope(t: ReportTemplate, input: ReportInput, chosen: number[]): number[] {
  if ((t.need === "chat" || t.need === "group") && input.chat) return [input.chat.id];
  return chosen;
}
