import { describe, expect, it } from "vitest";
import {
  bucketEnd,
  heatmapPoints,
  isEmptyChart,
  timeSeriesToEcharts,
  toAxisValue,
} from "@/features/tabs/charts";

const ts = {
  bucket: "month" as const,
  x: ["2024-01-01", "2024-02-01"],
  unit: "ms" as const,
  series: [{ key: "1", name: "Ann", person_id: 1, color_index: 0, values: [1000, null] }],
};

describe("tab chart transforms", () => {
  it("converts time series to echarts pairs (ms → s)", () => {
    const [s] = timeSeriesToEcharts(ts);
    expect(s?.data).toEqual([
      ["2024-01-01", 1],
      ["2024-02-01", null],
    ]);
    expect(s?.name).toBe("Ann");
  });
  it("flattens heatmaps", () => {
    expect(heatmapPoints({ x_labels: ["0", "1"], y_labels: ["Mon"], values: [[0, 5]] })).toEqual({
      data: [
        [0, 0, 0],
        [1, 0, 5],
      ],
      max: 5,
    });
  });
  it("detects empty charts and converts units", () => {
    expect(isEmptyChart({ series: [{ values: [0, null] }] })).toBe(true);
    expect(isEmptyChart({ series: [{ values: [0, 1] }] })).toBe(false);
    expect(toAxisValue(1500, "ms")).toBe(1.5);
    expect(toAxisValue(3, "count")).toBe(3);
  });
  it("computes bucket end dates", () => {
    expect(bucketEnd("2024-02-01", "month")).toBe("2024-02-29");
    expect(bucketEnd("2024-01-01", "week")).toBe("2024-01-07");
    expect(bucketEnd("2024-05-05", "day")).toBe("2024-05-05");
    expect(bucketEnd("2023-01-01", "year")).toBe("2023-12-31");
  });
});
