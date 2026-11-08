import { describe, expect, it } from "vitest";
import { flattenPages, type MessagePage } from "@/features/messages/api";
import {
  attachmentLabels,
  callLabel,
  highlightTerm,
  localDate,
  safeHref,
  shareLabel,
  systemLabel,
} from "@/features/messages/format";

describe("message row helpers", () => {
  it("labels attachments", () => {
    expect(
      attachmentLabels({
        type: "photo",
        attachments: { photos: 2, videos: 1, audios: 0, gifs: 0, stickers: 0 },
      }),
    ).toEqual(["📷 2 photos", "🎬 Video"]);
  });
  it("labels shares, calls and system events", () => {
    expect(shareLabel({ share: { kind: "reel", owner: "nasa" } })).toBe("🎞️ Shared reel · @nasa");
    expect(shareLabel({ share: null })).toBeNull();
    expect(callLabel({ call: { kind: "video", missed: true } })).toBe("📹 Video call · missed");
    expect(callLabel({ call: { kind: "audio", duration_s: 65, missed: false } })).toBe(
      "📞 Audio call · 1 min 5 s",
    );
    expect(systemLabel({ system_event: "rename", system_payload: "Crew" })).toBe(
      "ⓘ renamed the group: Crew",
    );
  });
  it("only allows http(s) links", () => {
    expect(safeHref("https://example.com/a")).toBe("https://example.com/a");
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref("not a url")).toBeNull();
    expect(safeHref(null)).toBeNull();
  });
  it("does not highlight regex searches", () => {
    expect(highlightTerm("abc", "plain")).toBe("abc");
    expect(highlightTerm("a.c", "regex")).toBeUndefined();
    expect(highlightTerm("", "word")).toBeUndefined();
  });
  it("computes the local date in a time zone", () => {
    expect(localDate("2024-03-01T23:30:00Z", "UTC")).toBe("2024-03-01");
    expect(localDate("2024-03-01T23:30:00Z", "Europe/Bratislava")).toBe("2024-03-02");
  });
});

describe("flattenPages", () => {
  it("concatenates pages and drops duplicates", () => {
    const m = (id: number) => ({ message_id: id }) as MessagePage["items"][number];
    const page = (ids: number[]) => ({ items: ids.map(m) }) as MessagePage;
    expect(flattenPages([page([1, 2]), page([2, 3])]).map((x) => x.message_id)).toEqual([1, 2, 3]);
    expect(flattenPages(undefined)).toEqual([]);
  });
});
