import { formatDuration } from "@/lib/format";
import type { MessageItem } from "@/features/messages/api";

/** Pure display helpers for Message Browser rows. */

/** Attachment counts as short labels: "📷 2 photos", "🎙️ Voice note". */
export function attachmentLabels(m: Pick<MessageItem, "attachments" | "type">): string[] {
  const a = m.attachments;
  const out: string[] = [];
  const add = (n: number, icon: string, one: string, many: string) => {
    if (n > 0) out.push(`${icon} ${n === 1 ? one : `${n} ${many}`}`);
  };
  add(a.photos, "📷", "Photo", "photos");
  add(a.videos, "🎬", "Video", "videos");
  add(a.audios, "🎙️", "Voice note", "voice notes");
  add(a.gifs, "🎞️", "GIF", "GIFs");
  add(a.stickers, "🏷️", "Sticker", "stickers");
  return out;
}

const SHARE_LABELS: Record<string, string> = {
  reel: "🎞️ Shared reel",
  post: "🖼️ Shared post",
  story: "📖 Shared story",
  profile: "👤 Shared profile",
  external: "🔗 Shared link",
  other: "↗️ Shared",
};

export function shareLabel(m: Pick<MessageItem, "share">): string | null {
  if (!m.share) return null;
  const base = SHARE_LABELS[m.share.kind ?? "other"] ?? SHARE_LABELS.other!;
  return m.share.owner ? `${base} · @${m.share.owner}` : base;
}

export function callLabel(m: Pick<MessageItem, "call">): string | null {
  if (!m.call) return null;
  const kind = m.call.kind === "video" ? "📹 Video call" : "📞 Audio call";
  if (m.call.missed) return `${kind} · missed`;
  return m.call.duration_s != null ? `${kind} · ${formatDuration(m.call.duration_s)}` : kind;
}

const SYSTEM_LABELS: Record<string, string> = {
  rename: "renamed the group",
  join: "added a member",
  leave: "left the group",
  removed: "removed a member",
  photo_change: "changed the group photo",
  theme_change: "changed the theme",
  emoji_change: "changed the quick reaction",
  nickname: "set a nickname",
  admin: "changed admins",
  call_event: "call notice",
  attachment: "sent an attachment",
  reaction_text: "reacted",
  story_reply: "replied to a story",
  story_mention: "mentioned you in a story",
  other: "system message",
};

export function systemLabel(
  m: Pick<MessageItem, "system_event" | "system_payload">,
): string | null {
  if (!m.system_event) return null;
  const base = SYSTEM_LABELS[m.system_event] ?? "system message";
  return m.system_payload ? `ⓘ ${base}: ${m.system_payload.replace(/\t/g, " → ")}` : `ⓘ ${base}`;
}

/** Only http(s) links are rendered as anchors; anything else stays plain text. */
export function safeHref(link: string | null | undefined): string | null {
  if (!link) return null;
  try {
    const u = new URL(link);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}

/** Text to highlight in bubbles: only plain/word modes (regex is not highlighted). */
export function highlightTerm(text: string, mode: string): string | undefined {
  if (!text || mode === "regex") return undefined;
  return text;
}

/** Local calendar date (YYYY-MM-DD) of an ISO timestamp in a time zone. */
export function localDate(ts: string, timeZone?: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts.slice(0, 10);
  const parts = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...(timeZone ? { timeZone } : {}),
  }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}
