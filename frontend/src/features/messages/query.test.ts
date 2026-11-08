import { describe, expect, it } from "vitest";
import { messagesHref, toMessageQuery } from "@/lib/drilldown";
import {
  activeFilterCount,
  cursorAt,
  emptyBrowserQuery,
  encodeCursor,
  parseBrowserParams,
  toApiQuery,
  writeBrowserParams,
} from "@/features/messages/query";

const parse = (s: string) => parseBrowserParams(new URLSearchParams(s));

describe("parseBrowserParams", () => {
  it("returns defaults for an empty URL", () => {
    expect(parse("")).toEqual(emptyBrowserQuery());
  });

  it("accepts the frontend MessageQuery spelling", () => {
    const q = parse("thread=12&sender=3&type=photo&q=ahoj&mode=word&case_sensitive=true&emoji=😂");
    expect(q.thread_ids).toEqual([12]);
    expect(q.sender_ids).toEqual([3]);
    expect(q.types).toEqual(["photo"]);
    expect(q.text).toBe("ahoj");
    expect(q.text_mode).toBe("word");
    expect(q.case_sensitive).toBe(true);
    expect(q.emoji).toBe("😂");
  });

  it("accepts the backend MessageQuery spelling incl. lists and extras", () => {
    const q = parse(
      "thread_ids=1,2&sender_ids=4,5&types=text,call&text=a.b&text_mode=regex&accent_insensitive=1&has_reactions=false&hour=7&message_ids=9,10&call_missed=true",
    );
    expect(q.thread_ids).toEqual([1, 2]);
    expect(q.sender_ids).toEqual([4, 5]);
    expect(q.types).toEqual(["text", "call"]);
    expect(q.text_mode).toBe("regex");
    expect(q.accent_insensitive).toBe(true);
    expect(q.has_reactions).toBe(false);
    expect(q.extra).toEqual({ hour: "7", message_ids: "9,10", call_missed: "true" });
  });

  it("ignores invalid values", () => {
    const q = parse("thread_id=abc&types=bogus&text_mode=fuzzy&order=sideways&context=-1&at=x");
    expect(q.thread_ids).toEqual([]);
    expect(q.types).toEqual([]);
    expect(q.text_mode).toBe("plain");
    expect(q.order).toBe("desc");
    expect(q.context).toBeNull();
    expect(q.at).toBeNull();
  });

  it("reads context / at / order", () => {
    const q = parse("context=42&at=40&order=asc");
    expect(q).toMatchObject({ context: 42, at: 40, order: "asc" });
  });
});

describe("writeBrowserParams", () => {
  it("round-trips and keeps global filters and unrelated params", () => {
    const base = new URLSearchParams("start=2023-01-01&tz=UTC&thread=5&q=old&foo=bar");
    const q = { ...parse(base.toString()), text: "new", sender_ids: [1, 2] };
    const out = writeBrowserParams(q, base);
    expect(out.get("start")).toBe("2023-01-01");
    expect(out.get("tz")).toBe("UTC");
    expect(out.get("foo")).toBe("bar");
    expect(out.get("thread")).toBeNull();
    expect(out.get("q")).toBeNull();
    expect(out.get("thread_id")).toBe("5");
    expect(out.get("sender_ids")).toBe("1,2");
    expect(out.get("text")).toBe("new");
    expect(parseBrowserParams(out)).toEqual(q);
  });

  it("writes nothing for defaults and drops text options without text", () => {
    const q = { ...emptyBrowserQuery(), case_sensitive: true, text_mode: "regex" as const };
    expect(writeBrowserParams(q).toString()).toBe("");
  });
});

describe("toApiQuery", () => {
  it("maps single and multiple ids to the right backend params", () => {
    expect(toApiQuery({ ...emptyBrowserQuery(), thread_ids: [3], sender_ids: [1, 2] })).toEqual({
      thread_id: 3,
      sender_ids: [1, 2],
    });
  });
  it("sends text options only with text, and passes extras through", () => {
    const q = { ...emptyBrowserQuery(), text: "x", case_sensitive: true, extra: { hour: "3" } };
    expect(toApiQuery(q)).toEqual({
      text: "x",
      text_mode: "plain",
      case_sensitive: true,
      hour: "3",
    });
    expect(toApiQuery({ ...emptyBrowserQuery(), case_sensitive: true })).toEqual({});
  });
});

describe("drilldown → URL → browser", () => {
  it("parses a backend drilldown passed through messagesHref", () => {
    const href = messagesHref(
      toMessageQuery({ thread_id: 7, sender_id: null, types: ["call"], message_ids: [1, 2] }),
    );
    const q = parse(href.split("?")[1] ?? "");
    expect(q.thread_ids).toEqual([7]);
    expect(q.sender_ids).toEqual([]);
    expect(q.types).toEqual(["call"]);
    expect(q.extra.message_ids).toBe("1,2");
  });
});

describe("activeFilterCount", () => {
  it("does not count a fixed thread", () => {
    const q = { ...emptyBrowserQuery(), thread_ids: [1], emoji: "😂", extra: { hour: "1" } };
    expect(activeFilterCount(q)).toBe(3);
    expect(activeFilterCount(q, true)).toBe(2);
  });
});

describe("cursors", () => {
  it("encodes like the backend (base64url, no padding)", () => {
    expect(encodeCursor(0, 41)).toBe("MDo0MQ");
    expect(atob("MDo0MQ")).toBe("0:41");
  });
  it("positions the cursor so the message itself is included", () => {
    expect(atob(cursorAt(10, "desc"))).toBe("0:11");
    expect(atob(cursorAt(10, "asc"))).toBe("0:9");
  });
});
