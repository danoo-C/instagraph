import { describe, expect, it } from "vitest";
import {
  barLayout,
  chipLayout,
  fitFontSize,
  font,
  layoutSlide,
  PAD,
  SLIDE_H,
  SLIDE_W,
  truncateText,
  wrapText,
  type Measure,
} from "@/features/wrapped/layout";
import type { Slide } from "@/features/wrapped/model";

/** Fake measure: every character is half the font size wide. */
const measure: Measure = (text, f) => {
  const size = Number(/(\d+)px/.exec(f)?.[1] ?? 10);
  return Array.from(text).length * size * 0.5;
};
const F10 = font(10); // 5px per char

describe("text helpers", () => {
  it("wraps words to the width", () => {
    expect(wrapText("aaa bbb ccc", 35, F10, measure)).toEqual(["aaa bbb", "ccc"]);
  });
  it("breaks words longer than a line", () => {
    expect(wrapText("abcdefghij", 20, F10, measure)).toEqual(["abcd", "efgh", "ij"]);
  });
  it("limits lines with an ellipsis", () => {
    const lines = wrapText("one two three four five six", 40, F10, measure, 2);
    expect(lines).toHaveLength(2);
    expect(lines[1]!.endsWith("…")).toBe(true);
    for (const l of lines) expect(measure(l, F10)).toBeLessThanOrEqual(40);
  });
  it("truncates to fit", () => {
    expect(truncateText("short", 100, F10, measure)).toBe("short");
    const t = truncateText("a very long name indeed", 50, F10, measure);
    expect(t.endsWith("…")).toBe(true);
    expect(measure(t, F10)).toBeLessThanOrEqual(50);
  });
  it("fits the font size", () => {
    // 4 chars at size s → 2s wide; must fit 400 → s ≤ 200.
    expect(fitFontSize("1234", 400, 260, 72, measure)).toBe(200);
    expect(fitFontSize("x".repeat(100), 400, 260, 72, measure)).toBe(72);
  });
});

describe("barLayout", () => {
  it("scales bars to the tallest and bottom-aligns them", () => {
    const rects = barLayout([0, 5, 10], { x: 0, y: 0, w: 320, h: 100 }, 10);
    expect(rects).toHaveLength(3);
    expect(rects[0]!.w).toBe(100);
    expect(rects[1]!.x).toBe(110);
    expect(rects[2]!.h).toBe(100);
    expect(rects[1]!.h).toBe(50);
    expect(rects[1]!.y).toBe(50);
    expect(rects[0]!.h).toBe(4); // stub for zero
  });
  it("handles empty and all-zero input", () => {
    expect(barLayout([], { x: 0, y: 0, w: 10, h: 10 })).toEqual([]);
    expect(barLayout([0, 0], { x: 0, y: 0, w: 10, h: 10 }, 0).every((r) => r.h === 4)).toBe(true);
  });
});

describe("chipLayout", () => {
  it("wraps chips into rows", () => {
    const r = chipLayout([100, 100, 100], 230, 50, 20);
    expect(r.map((c) => c.row)).toEqual([0, 0, 1]);
    expect(r[1]!.x).toBe(120);
    expect(r[2]!).toMatchObject({ x: 0, y: 70 });
  });
});

describe("layoutSlide", () => {
  const theme = { from: "#000", to: "#111", fg: "#fff", muted: "#ccc", accent: "#ff0" };
  const slide: Slide = {
    key: "people",
    kicker: "Your people",
    title: "Your top 5",
    footer: "Instagraph Wrapped 2025 · All chats",
    theme,
    blocks: [
      { kind: "big", text: "6,886" },
      {
        kind: "list",
        items: [1, 2, 3].map((i) => ({
          rank: i,
          label: `Person with a pretty long name ${i}`,
          value: "1,234",
        })),
      },
      {
        kind: "bars",
        values: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        labels: "JFMAMJJASOND".split(""),
      },
      { kind: "chips", items: ["😂 12", "❤ 9", "🔥 3"] },
      { kind: "quote", text: "hello there", meta: "Jana · Chat · Jan 1" },
    ],
  };
  const ops = layoutSlide(slide, measure);

  it("keeps everything inside the 1080×1920 canvas and the side padding", () => {
    for (const o of ops) {
      expect(o.y).toBeGreaterThanOrEqual(0);
      expect(o.y).toBeLessThanOrEqual(SLIDE_H);
      if (o.op === "rect") {
        expect(o.x).toBeGreaterThanOrEqual(PAD - 0.01);
        expect(o.x + o.w).toBeLessThanOrEqual(SLIDE_W - PAD + 0.01);
      }
      if (o.op === "text" && o.align === "left") {
        expect(o.x + measure(o.text, o.font)).toBeLessThanOrEqual(SLIDE_W - PAD + 0.01);
      }
    }
  });
  it("draws kicker first, footer last, list labels truncated", () => {
    const texts = ops.filter((o) => o.op === "text");
    expect(texts[0]).toMatchObject({ text: "YOUR PEOPLE", align: "center", x: SLIDE_W / 2 });
    expect(texts[texts.length - 1]).toMatchObject({ text: slide.footer, y: SLIDE_H - 110 });
    expect(texts.some((t) => t.op === "text" && t.text.endsWith("…"))).toBe(true);
    expect(ops.filter((o) => o.op === "rect" && o.color === theme.accent)).toHaveLength(12);
  });
  it("centers short content vertically", () => {
    const small = layoutSlide({ ...slide, blocks: [{ kind: "big", text: "42" }] }, measure);
    const big = small.find((o) => o.op === "text" && o.text === "42")!;
    expect(big.y).toBeGreaterThan(700);
    expect(big.y).toBeLessThan(1300);
  });
});
