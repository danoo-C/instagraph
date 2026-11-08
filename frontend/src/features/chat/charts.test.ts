import { describe, expect, it } from "vitest";
import {
  bucketRange,
  diffMatrix,
  heatmapPoints,
  maxAbs,
  normalizeMatrix,
  radarIndicators,
  replyBoxRows,
  shortSeconds,
  sparklinePoints,
  trendArrow,
} from "@/features/chat/charts";
import type { PersonReplyStats } from "@/features/chat/types";

const person = { person_id: 1, name: "A", color_index: 0, is_owner: true };

describe("bucketRange", () => {
  it("handles each bucket", () => {
    expect(bucketRange("2024-02-05", "day")).toEqual({ start: "2024-02-05", end: "2024-02-05" });
    expect(bucketRange("2024-02-26", "week")).toEqual({ start: "2024-02-26", end: "2024-03-03" });
    expect(bucketRange("2024-02-01", "month")).toEqual({ start: "2024-02-01", end: "2024-02-29" });
    expect(bucketRange("2023-12-01", "month")).toEqual({ start: "2023-12-01", end: "2023-12-31" });
    expect(bucketRange("2023-01-01", "year")).toEqual({ start: "2023-01-01", end: "2023-12-31" });
  });
});

describe("matrices", () => {
  it("heatmap points are [x, y, v]", () => {
    expect(
      heatmapPoints([
        [1, 2],
        [3, 4],
      ]),
    ).toEqual([
      [0, 0, 1],
      [1, 0, 2],
      [0, 1, 3],
      [1, 1, 4],
    ]);
  });
  it("normalizes and diffs by share", () => {
    expect(normalizeMatrix([[0, 0]])).toEqual([[0, 0]]);
    expect(normalizeMatrix([[1, 3]])).toEqual([[0.25, 0.75]]);
    const d = diffMatrix([[1, 1]], [[0, 10]]);
    expect(d).toEqual([[0.5, -0.5]]);
    expect(maxAbs(d)).toBe(0.5);
  });
});

describe("replyBoxRows", () => {
  it("converts ms to seconds and returns null without replies", () => {
    const base: PersonReplyStats = {
      person,
      replies: 3,
      median_ms: 60_000,
      p25_ms: 10_000,
      p75_ms: 120_000,
      p90_ms: 600_000,
      fastest_ms: 2_000,
      turns: 5,
      replied_turns: 3,
      unanswered_turns: 2,
      drilldown: {},
    };
    expect(replyBoxRows([base, { ...base, replies: 0, median_ms: null }])).toEqual([
      [2, 10, 60, 120, 600],
      null,
    ]);
  });
});

describe("misc", () => {
  it("shortSeconds", () => {
    expect(shortSeconds(1)).toBe("1 s");
    expect(shortSeconds(120)).toBe("2 min");
    expect(shortSeconds(43200)).toBe("12 h");
    expect(shortSeconds(172800)).toBe("2 d");
  });
  it("trendArrow", () => {
    expect(trendArrow("rising").symbol).toBe("↗");
    expect(trendArrow("fading").symbol).toBe("↘");
    expect(trendArrow("stable").label).toBe("Stable");
  });
  it("sparklinePoints", () => {
    expect(sparklinePoints([], 10, 10)).toBe("");
    expect(sparklinePoints([0, 10], 10, 10)).toBe("1,9 9,1");
    expect(sparklinePoints([0, 0], 10, 10)).toBe("1,9 9,9");
  });
  it("radarIndicators use a symmetric padded limit", () => {
    const ind = radarIndicators([{ key: "a", label: "A" }], [[2], [-1], [null]]);
    expect(ind).toEqual([{ name: "A", min: -2.4, max: 2.4 }]);
  });
});
