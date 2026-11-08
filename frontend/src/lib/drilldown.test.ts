import { describe, expect, it } from "vitest";
import { parseBrowserParams, toApiQuery } from "@/features/messages/query";
import {
  drilldownQuery,
  messagesHref,
  toMessageQuery,
  type ApiMessageQuery,
} from "@/lib/drilldown";

const paramsOf = (href: string) => new URLSearchParams(href.split("?")[1] ?? "");

describe("toMessageQuery", () => {
  it("keeps backend keys, drops nulls/empties and joins lists", () => {
    expect(
      toMessageQuery({
        thread_id: 5,
        sender_id: null,
        sender_ids: [1, 2],
        types: ["photo", "video"],
        start: "2024-01-01",
        end: null,
        text: "",
        hour: 0,
        message_ids: [],
        emoji: "😂",
      }),
    ).toEqual({
      thread_id: "5",
      sender_ids: "1,2",
      types: "photo,video",
      start: "2024-01-01",
      hour: "0",
      emoji: "😂",
    });
    expect(toMessageQuery(undefined)).toEqual({});
    expect(toMessageQuery({ thread_ids: [] })).toEqual({});
  });

  it("canonicalises frontend aliases and lets extras win", () => {
    expect(toMessageQuery({ thread: 3, q: "hi", mode: "word", type: "call" })).toEqual({
      thread_id: "3",
      text: "hi",
      text_mode: "word",
      types: "call",
    });
    expect(toMessageQuery({ thread_id: 1, start: "2020-01-01" }, { start: "2024-01-01" })).toEqual({
      thread_id: "1",
      start: "2024-01-01",
    });
    // An alias in `extra` overrides the backend key instead of being unioned with it.
    expect(toMessageQuery({ thread_id: 1 }, { thread: 2 })).toEqual({ thread_id: "2" });
    // An empty extra removes the key.
    expect(toMessageQuery({ emoji: "😂", hour: 3 }, { emoji: "" })).toEqual({ hour: "3" });
  });

  it("drilldownQuery is undefined only without a drilldown", () => {
    expect(drilldownQuery(null)).toBeUndefined();
    expect(drilldownQuery(undefined)).toBeUndefined();
    expect(drilldownQuery({ sender_id: null })).toEqual({});
    expect(drilldownQuery({ sender_id: 4 })).toEqual({ sender_id: "4" });
  });
});

describe("messagesHref", () => {
  it("keeps global filters, drops page params, lets the query override dates", () => {
    const current = new URLSearchParams("start=2020-01-01&tz=UTC&metric=words&people=1");
    const p = paramsOf(
      messagesHref({ thread: 4, start: "2024-05-05", end: "2024-05-05" }, current),
    );
    expect(p.get("thread_id")).toBe("4");
    expect(p.get("thread")).toBeNull();
    expect(p.get("start")).toBe("2024-05-05");
    expect(p.get("end")).toBe("2024-05-05");
    expect(p.get("tz")).toBe("UTC");
    expect(p.get("metric")).toBeNull();
  });

  it("accepts a raw backend drilldown (nulls, arrays)", () => {
    expect(messagesHref({ thread_id: 7, sender_id: null, types: ["call"] })).toBe(
      "/search?thread_id=7&types=call",
    );
    expect(messagesHref({})).toBe("/search");
  });
});

describe("drilldown round trip", () => {
  it("backend drilldown → URL → parseBrowserParams → toApiQuery yields the same params", () => {
    const drilldowns: ApiMessageQuery[] = [
      { thread_id: 7, sender_id: 3, types: ["photo", "video"], hour: 13, weekday: 2 },
      { thread_ids: [1, 2], sender_ids: [4, 5], has_reactions: true, emoji: "😂" },
      { thread_id: 9, text: "hello", text_mode: "word", case_sensitive: true },
      { message_ids: [10, 11, 12], call_missed: false, reaction_emoji: "❤️", reactor_id: 2 },
      { thread_id: 1, start: "2024-01-01", end: "2024-01-31", month: 1, link_domain: "x.com" },
    ];
    for (const d of drilldowns) {
      const href = messagesHref(d);
      const params = paramsOf(href);
      const api = toApiQuery(parseBrowserParams(params));

      // start/end travel as global filter params (handled by useFilters, not toApiQuery).
      const { start, end, ...rest } = d;
      if (start) expect(params.get("start")).toBe(start);
      if (end) expect(params.get("end")).toBe(end);

      // Compare as strings: URL params are untyped; the backend coerces.
      const norm = (o: Record<string, unknown>) =>
        Object.fromEntries(
          Object.entries(o)
            .filter(([, v]) => v != null && !(Array.isArray(v) && v.length === 0))
            .map(([k, v]) => [k, Array.isArray(v) ? v.join(",") : String(v)]),
        );
      expect(norm(api)).toEqual(norm(rest));
    }
  });

  it("frontend aliases round-trip to the backend names", () => {
    const api = toApiQuery(
      parseBrowserParams(paramsOf(messagesHref({ thread: 2, sender: 3, type: "text", q: "x" }))),
    );
    expect(api).toEqual({
      thread_id: 2,
      sender_id: 3,
      types: ["text"],
      text: "x",
      text_mode: "plain",
    });
  });
});
