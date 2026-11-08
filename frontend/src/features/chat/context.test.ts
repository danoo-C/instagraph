import { describe, expect, it } from "vitest";
import { buildChatContext, parseChatId } from "@/features/chat/context";
import { parseListParams, sortingToParams } from "@/pages/Chats/listParams";

describe("chat context", () => {
  it("parses ids", () => {
    expect(parseChatId("12")).toBe(12);
    expect(parseChatId("x1")).toBeNaN();
  });
  it("builds participants lookup", () => {
    const ctx = buildChatContext("5", {
      dataset_version: 1,
      thread_id: 5,
      title: "T",
      kind: "one_to_one",
      category: "inbox",
      is_group: false,
      participants: [
        { person_id: 8, name: "Me", color_index: 0, is_owner: true },
        { person_id: 9, name: "You", color_index: 1, is_owner: false },
      ],
      span: {},
      span_days: 1,
      total_messages: 2,
      my_messages: 1,
      my_share: 0.5,
      drilldown: { thread_id: 5 },
    });
    expect(ctx.ownerId).toBe(8);
    expect(ctx.isGroup).toBe(false);
    expect(ctx.personById(9)?.name).toBe("You");
    expect(ctx.colorOf(8)).toBe("#0072B2");
    expect(buildChatContext("5", undefined).isGroup).toBeUndefined();
  });
});

describe("chats list params", () => {
  it("parses defaults and validates sort", () => {
    expect(parseListParams(new URLSearchParams())).toEqual({
      q: "",
      sort: "messages",
      order: "desc",
    });
    expect(parseListParams(new URLSearchParams("sort=bogus&order=asc&q=a"))).toEqual({
      q: "a",
      sort: "messages",
      order: "asc",
    });
  });
  it("maps sorting state", () => {
    expect(sortingToParams([{ id: "title", desc: false }])).toEqual({
      sort: "title",
      order: "asc",
    });
    expect(sortingToParams([])).toBeNull();
    expect(sortingToParams([{ id: "sparkline", desc: true }])).toBeNull();
  });
});
