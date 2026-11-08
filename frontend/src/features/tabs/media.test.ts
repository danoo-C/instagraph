import { describe, expect, it } from "vitest";
import { chunkRows, galleryLayout } from "@/features/tabs/media";
import { chordGraph, membershipBars } from "@/features/tabs/group";
import { formatAwardValue } from "@/features/tabs/awards";

describe("galleryLayout", () => {
  it("fits as many min-size cells as possible", () => {
    expect(galleryLayout(1000, 120, 8)).toEqual({ columns: 7, cell: 136 });
    expect(galleryLayout(100, 120, 8).columns).toBe(1);
    expect(galleryLayout(0).columns).toBe(1);
  });
  it("chunks rows", () => {
    expect(chunkRows([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]]);
    expect(chunkRows([], 3)).toEqual([]);
  });
});

const p = (id: number, ci = id) => ({
  person_id: id,
  name: `P${id}`,
  color_index: ci,
  is_owner: false,
});

describe("group helpers", () => {
  it("builds chord nodes/links, skipping zero and self links", () => {
    const g = chordGraph(
      {
        people: [p(1), p(2)],
        matrix: { x_labels: [], y_labels: [], values: [] },
        links: [
          { source: 1, target: 2, value: 10 },
          { source: 2, target: 1, value: 5 },
          { source: 1, target: 1, value: 3 },
          { source: 2, target: 2, value: 0 },
        ],
      },
      { 1: 100, 2: 25 },
    );
    expect(g.nodes.map((n) => n.id)).toEqual(["1", "2"]);
    expect(g.nodes[0]!.symbolSize).toBeGreaterThan(g.nodes[1]!.symbolSize);
    expect(g.links).toHaveLength(2);
    expect(g.links[0]!.lineStyle.width).toBe(10);
  });
  it("fills open membership intervals with the span", () => {
    const bars = membershipBars(
      [
        { person: p(1), intervals: [{ start: null, end: "2024-01-02T00:00:00Z" }] },
        { person: p(2), intervals: [] },
      ],
      0,
      1e13,
    );
    expect(bars).toEqual([
      [0, 0, Date.parse("2024-01-02T00:00:00Z")],
      [1, 0, 1e13],
    ]);
  });
});

describe("formatAwardValue", () => {
  it("formats by unit", () => {
    expect(formatAwardValue(0.25, "ratio")).toMatch(/25/);
    expect(formatAwardValue(90000, "ms")).toBe("1 min 30 s");
    expect(formatAwardValue(null, "count")).toBe("—");
  });
});
