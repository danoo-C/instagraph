import { describe, expect, it } from "vitest";
import { formatTokens, parseSse } from "@/features/ai/api";
import { parseBlocks, parseInline } from "@/features/ai/markdownParse";
import { applyEvent, EMPTY_USAGE, type LiveTurn } from "@/features/ai/turn";

describe("parseSse", () => {
  it("parses complete events and keeps the rest", () => {
    const chunk =
      'event: text\r\ndata: {"type":"text","text":"Hi"}\r\n\r\n: ping\r\n\r\nevent: done\r\ndata: {"ty';
    const { events, rest } = parseSse(chunk);
    expect(events).toEqual([{ type: "text", text: "Hi" }]);
    const more = parseSse(rest + 'pe":"done","status":"done"}\n\n');
    expect(more.events).toEqual([{ type: "done", status: "done" }]);
    expect(more.rest).toBe("");
  });

  it("ignores non-JSON data", () => {
    expect(parseSse("data: nope\n\n").events).toEqual([]);
  });
});

describe("parseInline", () => {
  it("finds citations and emphasis", () => {
    expect(parseInline("See [[msg:12]] in [[chat:3]], **bold** and *it* and `x`.")).toEqual([
      { kind: "text", text: "See " },
      { kind: "msg", id: 12 },
      { kind: "text", text: " in " },
      { kind: "chat", id: 3 },
      { kind: "text", text: ", " },
      { kind: "bold", text: "bold" },
      { kind: "text", text: " and " },
      { kind: "italic", text: "it" },
      { kind: "text", text: " and " },
      { kind: "code", text: "x" },
      { kind: "text", text: "." },
    ]);
  });

  it("leaves HTML and odd brackets as text", () => {
    expect(parseInline('<img src=x onerror="a()"> [[msg:abc]]')).toEqual([
      { kind: "text", text: '<img src=x onerror="a()"> [[msg:abc]]' },
    ]);
  });

  it("does not treat snake_case as italics", () => {
    expect(parseInline("my_share_value")).toEqual([{ kind: "text", text: "my_share_value" }]);
  });
});

describe("parseBlocks", () => {
  it("splits paragraphs, headings, lists and code", () => {
    const src = "# Title\nfirst line\nsecond line\n\n- a\n- b\n\n1. one\n2. two\n\n```\ncode\n```";
    expect(parseBlocks(src)).toEqual([
      { kind: "h", lines: ["Title"] },
      { kind: "p", lines: ["first line", "second line"] },
      { kind: "ul", items: ["a", "b"] },
      { kind: "ol", items: ["one", "two"] },
      { kind: "code", text: "code" },
    ]);
  });

  it("handles a list right after a paragraph", () => {
    expect(parseBlocks("Intro:\n- x\n- y\nafter")).toEqual([
      { kind: "p", lines: ["Intro:"] },
      { kind: "ul", items: ["x", "y"] },
      { kind: "p", lines: ["after"] },
    ]);
  });
});

describe("applyEvent", () => {
  const start: LiveTurn = {
    user_text: "q",
    answer: "",
    tools: [],
    usage: EMPTY_USAGE,
    status: "running",
    error: null,
  };

  it("builds a turn from stream events", () => {
    let t = applyEvent(start, { type: "tool_start", id: "1", name: "list_chats", args: {} });
    t = applyEvent(t, {
      type: "tool_end",
      id: "1",
      name: "list_chats",
      summary: "3 items",
      is_error: false,
      truncated: true,
    });
    t = applyEvent(t, { type: "text", text: "Hel" });
    t = applyEvent(t, { type: "text", text: "lo" });
    t = applyEvent(t, { type: "usage", ...EMPTY_USAGE, input_tokens: 5 });
    t = applyEvent(t, { type: "done", status: "done" });
    expect(t.answer).toBe("Hello");
    expect(t.tools[0]).toMatchObject({ name: "list_chats", summary: "3 items", truncated: true });
    expect(t.usage.input_tokens).toBe(5);
    expect(t.status).toBe("done");
  });

  it("records errors", () => {
    const t = applyEvent(start, { type: "error", code: "x", message: "Rate limited" });
    expect(t).toMatchObject({ status: "error", error: "Rate limited" });
  });
});

describe("formatTokens", () => {
  it("abbreviates", () => {
    expect(formatTokens(950)).toBe("950");
    expect(formatTokens(12_400)).toBe("12.4k");
    expect(formatTokens(250_000)).toBe("250k");
  });
});

describe("reports", () => {
  it("every report builds a prompt and a title", async () => {
    const { REPORTS } = await import("@/features/ai/reports");
    const input = { chat: { id: 7, title: "Jana" }, year: 2024 };
    expect(REPORTS.map((r) => r.id)).toEqual([
      "chat-change",
      "effort",
      "personality",
      "year",
      "group",
    ]);
    for (const r of REPORTS) {
      expect(r.prompt(input).length).toBeGreaterThan(40);
      expect(r.label(input)).not.toContain("…");
    }
    expect(REPORTS[0]!.prompt(input)).toContain("Jana (chat_id 7)");
    expect(REPORTS[3]!.prompt(input)).toContain("2024");
  });

  it("chat reports read only the picked chat", async () => {
    const { REPORTS, reportScope } = await import("@/features/ai/reports");
    const byId = Object.fromEntries(REPORTS.map((r) => [r.id, r]));
    const input = { chat: { id: 7, title: "Jana" }, year: 2024 };
    expect(reportScope(byId.effort!, input, [1, 2])).toEqual([7]);
    expect(reportScope(byId.group!, input, [1, 2])).toEqual([7]);
    expect(reportScope(byId.personality!, input, [1, 2])).toEqual([1, 2]);
    expect(reportScope(byId.year!, input, [3])).toEqual([3]);
  });
});
