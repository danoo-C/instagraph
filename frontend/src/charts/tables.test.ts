import { describe, expect, it } from "vitest";
import { categoryTable, heatmapTable, timeSeriesTable } from "@/charts/tables";

describe("chart → CSV tables", () => {
  it("time series: date column + one column per series", () => {
    const ts = {
      bucket: "month",
      x: ["2024-01-01", "2024-02-01"],
      series: [
        { key: "1", name: "Ann", values: [1000, null] },
        { key: "2", name: "Bob", values: [3, 4] },
      ],
    };
    expect(timeSeriesTable(ts)).toEqual({
      headers: ["date", "Ann", "Bob"],
      rows: [
        ["2024-01-01", 1000, 3],
        ["2024-02-01", null, 4],
      ],
    });
    expect(timeSeriesTable(ts, "Month")?.headers[0]).toBe("Month");
    expect(timeSeriesTable(undefined)).toBeNull();
  });

  it("category chart: category column + one column per series", () => {
    const chart = { categories: ["x", "y"], series: [{ key: "1", name: "A", values: [1, null] }] };
    expect(categoryTable(chart, "Cat")).toEqual({
      headers: ["Cat", "A"],
      rows: [
        ["x", 1],
        ["y", null],
      ],
    });
    expect(categoryTable(null)).toBeNull();
  });

  it("heatmap: row label + one column per x label", () => {
    expect(
      heatmapTable(
        { x_labels: ["0", "1"], y_labels: ["Mon", "Tue"], values: [[0, 5], [2]] },
        "day",
      ),
    ).toEqual({
      headers: ["day", "0", "1"],
      rows: [
        ["Mon", 0, 5],
        ["Tue", 2],
      ],
    });
  });
});
