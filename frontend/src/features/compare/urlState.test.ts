import { describe, expect, it } from "vitest";
import {
  addId,
  canCompare,
  compareQuery,
  distinctColorIndexes,
  parseCompareState,
  parseIds,
  removeId,
  switchMode,
  writeCompareState,
} from "@/features/compare/urlState";

describe("parseIds", () => {
  it("parses, dedupes, drops junk and caps at 4", () => {
    expect(parseIds("3, 1,x,3,7,-2,9,10")).toEqual([3, 1, 7, 9]);
    expect(parseIds("")).toEqual([]);
    expect(parseIds(null)).toEqual([]);
    expect(parseIds("0")).toEqual([0]);
  });
});

describe("parseCompareState", () => {
  it("reads chats (also a single chat from the chat header button)", () => {
    expect(parseCompareState(new URLSearchParams("chats=12"))).toEqual({
      mode: "chats",
      ids: [12],
    });
  });
  it("reads people", () => {
    expect(parseCompareState(new URLSearchParams("people=1,2&tz=UTC"))).toEqual({
      mode: "people",
      ids: [1, 2],
    });
  });
  it("keeps the people mode with an empty selection", () => {
    expect(parseCompareState(new URLSearchParams("people="))).toEqual({ mode: "people", ids: [] });
  });
  it("defaults to chats and prefers non-empty chats", () => {
    expect(parseCompareState(new URLSearchParams(""))).toEqual({ mode: "chats", ids: [] });
    expect(parseCompareState(new URLSearchParams("chats=1,2&people=3,4")).mode).toBe("chats");
  });
});

describe("writeCompareState", () => {
  it("keeps other params and replaces the selection", () => {
    const next = writeCompareState(new URLSearchParams("tz=UTC&chats=1"), {
      mode: "people",
      ids: [4, 5],
    });
    expect(next.get("tz")).toBe("UTC");
    expect(next.has("chats")).toBe(false);
    expect(next.get("people")).toBe("4,5");
  });
  it("round-trips", () => {
    const s = { mode: "chats" as const, ids: [9, 3, 4] };
    expect(parseCompareState(writeCompareState(new URLSearchParams(), s))).toEqual(s);
  });
});

describe("state transitions", () => {
  it("adds, removes, caps and switches mode", () => {
    let s = { mode: "chats" as const, ids: [1] } as Parameters<typeof addId>[0];
    expect(canCompare(s)).toBe(false);
    s = addId(s, 2);
    expect(canCompare(s)).toBe(true);
    s = addId(addId(addId(s, 2), 3), 4);
    expect(s.ids).toEqual([1, 2, 3, 4]);
    expect(addId(s, 5)).toBe(s);
    expect(removeId(s, 3).ids).toEqual([1, 2, 4]);
    expect(switchMode(s, "people")).toEqual({ mode: "people", ids: [] });
    expect(switchMode(s, "chats")).toBe(s);
    expect(compareQuery(s)).toEqual({ chats: "1,2,3,4" });
    expect(compareQuery({ mode: "people", ids: [7, 8] })).toEqual({ people: "7,8" });
  });
});

describe("distinctColorIndexes", () => {
  it("keeps own colors and resolves collisions", () => {
    expect(distinctColorIndexes([0, 26, 3], 10)).toEqual([0, 6, 3]);
    expect(distinctColorIndexes([1, 1, null], 10)).toEqual([1, 0, 2]);
  });
});
