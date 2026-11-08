import { describe, expect, it } from "vitest";
import { bucketEnd, seriesColor } from "@/features/overview/query";
import { isEmptySeries } from "@/features/overview/tables";
import { tileDisplay } from "@/features/overview/tiles";
import {
  bumpOption,
  calendarOption,
  punchCardData,
  sentReceivedOption,
  timelineOption,
  toPercentStack,
} from "@/features/overview/transforms";
import type { ApiTimeSeries } from "@/features/overview/types";
import { OTHERS_COLOR, colorForIndex } from "@/lib/colors";

const ts = (series: ApiTimeSeries["series"], x = ["2024-01-01", "2024-02-01"]): ApiTimeSeries => ({
  bucket: "month",
  x,
  series,
});

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- inspecting ECharts options
const seriesOf = (o: any) => o.series as any[];

describe("bucketEnd", () => {
  it("computes inclusive bucket ends", () => {
    expect(bucketEnd("2024-02-01", "month")).toBe("2024-02-29");
    expect(bucketEnd("2023-12-25", "week")).toBe("2023-12-31");
    expect(bucketEnd("2024-03-05", "day")).toBe("2024-03-05");
    expect(bucketEnd("2024-01-01", "year")).toBe("2024-12-31");
  });
});

describe("seriesColor", () => {
  it("uses gray for others and the person color index otherwise", () => {
    expect(seriesColor({ key: "others" })).toBe(OTHERS_COLOR);
    expect(seriesColor({ key: "12", person_id: 3, color_index: 4 })).toBe(colorForIndex(4));
  });
});

describe("timelineOption", () => {
  it("stacks one area per series with ids = series keys", () => {
    const o = timelineOption(
      ts([
        { key: "1", name: "Jana", person_id: 2, color_index: 1, values: [3, 4] },
        { key: "others", name: "Others", values: [1, 0] },
      ]),
    );
    const s = seriesOf(o);
    expect(s.map((x) => x.id)).toEqual(["1", "others"]);
    expect(s.every((x) => x.stack === "total" && x.type === "line" && x.areaStyle)).toBe(true);
    expect(s[1].color).toBe(OTHERS_COLOR);
  });
});

describe("sentReceivedOption", () => {
  it("mirrors received below zero", () => {
    const o = sentReceivedOption(
      ts([
        { key: "sent", name: "Sent", values: [2, null] },
        { key: "received", name: "Received", values: [5, 1] },
      ]),
    );
    const s = seriesOf(o);
    expect(s[0].data).toEqual([2, null]);
    expect(s[1].data).toEqual([-5, -1]);
  });
});

describe("toPercentStack", () => {
  it("normalizes each bucket to 1 and keeps empty buckets at 0", () => {
    const shares = toPercentStack(
      ts(
        [
          { key: "text", name: "text", values: [3, 0, 1] },
          { key: "photo", name: "photo", values: [1, 0, null] },
        ],
        ["a", "b", "c"],
      ),
    );
    expect(shares).toEqual([
      [0.75, 0, 1],
      [0.25, 0, 0],
    ]);
  });
});

describe("bumpOption", () => {
  it("hides ranks beyond the cutoff", () => {
    const o = bumpOption(ts([{ key: "7", name: "G", values: [1, 20] }]), [], 15);
    expect(seriesOf(o)[0].data).toEqual([1, null]);
  });
});

describe("punchCardData", () => {
  it("flattens values[y][x] into [x, y, v]", () => {
    expect(punchCardData({ x_labels: ["0", "1"], y_labels: ["Mon"], values: [[4, 5]] })).toEqual([
      [0, 0, 4],
      [1, 0, 5],
    ]);
  });
});

describe("calendarOption", () => {
  it("creates one calendar per year, newest first", () => {
    const o = calendarOption(
      {
        days: [
          { date: "2023-05-01", value: 2 },
          { date: "2024-01-02", value: 7 },
        ],
        max_value: 7,
      },
      [2023, 2024],
      ["#000", "#111", "#222"],
    );
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- inspecting ECharts options
    const cals = o.calendar as any[];
    expect(cals.map((c) => c.range)).toEqual(["2024", "2023"]);
    expect(seriesOf(o)[0].data).toEqual([["2024-01-02", 7]]);
    expect(seriesOf(o)[1].calendarIndex).toBe(1);
  });
});

describe("isEmptySeries", () => {
  it("treats all-zero/null series as empty", () => {
    expect(isEmptySeries(ts([{ key: "a", name: "A", values: [0, null] }]))).toBe(true);
    expect(isEmptySeries(ts([{ key: "a", name: "A", values: [0, 2] }]))).toBe(false);
  });
});

describe("tileDisplay", () => {
  it("maps units to Tile formats", () => {
    const base = { key: "k", label: "L" };
    expect(tileDisplay({ ...base, value: 0.25, unit: "ratio" })).toEqual({
      value: 0.25,
      format: "percent",
    });
    expect(tileDisplay({ ...base, value: 90_000, unit: "ms" })).toEqual({
      value: 90,
      format: "duration",
    });
    expect(tileDisplay({ ...base, value: null })).toEqual({ value: null, format: "text" });
  });
});
