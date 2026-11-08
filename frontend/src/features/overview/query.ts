import type { ApiChatRef, ApiSeries, Bucket } from "@/features/overview/types";
import { OTHERS_COLOR, personColor } from "@/lib/colors";

function parseIso(iso: string): Date {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number) as [number, number, number];
  return new Date(Date.UTC(y, m - 1, d));
}

function isoOf(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** Inclusive last day of the bucket that starts at `start` (ISO date). */
export function bucketEnd(start: string, bucket: Bucket): string {
  const d = parseIso(start);
  switch (bucket) {
    case "day":
      return isoOf(d);
    case "week":
      return isoOf(new Date(d.getTime() + 6 * 86_400_000));
    case "month":
      return isoOf(new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)));
    case "year":
      return `${d.getUTCFullYear()}-12-31`;
  }
}

/** `{start, end}` of one bucket, for drilldowns from time-series charts. */
export function bucketRange(start: string, bucket: Bucket): { start: string; end: string } {
  return { start: start.slice(0, 10), end: bucketEnd(start, bucket) };
}

/** Color of a series: person color when it stands for a person, gray for "others". */
export function seriesColor(s: Pick<ApiSeries, "key" | "person_id" | "color_index">): string {
  if (s.key === "others") return OTHERS_COLOR;
  if (s.color_index != null || s.person_id != null)
    return personColor({ id: s.person_id, color_index: s.color_index });
  return personColor(`series:${s.key}`);
}

/** Color of a chat: the other person's color for 1:1 chats, a hashed color for groups. */
export function chatColor(chat: ApiChatRef): string {
  if (chat.person)
    return personColor({ id: chat.person.person_id, color_index: chat.person.color_index });
  return personColor(`series:${chat.thread_id}`);
}
