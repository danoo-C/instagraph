import { describe, expect, it } from "vitest";
import {
  formatCompact,
  formatDate,
  formatDateRange,
  formatDuration,
  formatNumber,
  formatPercent,
  formatWeekday,
} from "@/lib/format";

describe("formatDuration", () => {
  it.each([
    [11520, "3 h 12 min"],
    [45, "45 s"],
    [60, "1 min"],
    [3600, "1 h"],
    [3630, "1 h"],
    [90061, "1 d 1 h"],
    [0, "0 s"],
    [0.3, "< 1 s"],
    [-125, "-2 min 5 s"],
  ])("%s → %s", (s, expected) => {
    expect(formatDuration(s)).toBe(expected);
  });
  it("handles null", () => expect(formatDuration(null)).toBe("—"));
});

describe("number formatting", () => {
  it("formats numbers and percents", () => {
    expect(formatNumber(12345, {}, "en-US")).toBe("12,345");
    expect(formatNumber(undefined)).toBe("—");
    expect(formatPercent(0.425, 1, "en-US")).toBe("42.5%");
    expect(formatCompact(1_234_567, "en-US")).toBe("1.2M");
  });
});

describe("date formatting", () => {
  it("treats plain ISO dates as local dates", () => {
    expect(formatDate("2024-03-05", { dateStyle: "medium" }, "en-US")).toBe("Mar 5, 2024");
  });
  it("labels ranges", () => {
    expect(formatDateRange(null, null)).toBe("All time");
    expect(formatDateRange("2023-01-01", "2023-12-31", "en-US")).toMatch(/Jan 1.*Dec 31, 2023/);
  });
  it("weekday is ISO (0 = Monday)", () => {
    expect(formatWeekday(0, "short", "en-US")).toBe("Mon");
    expect(formatWeekday(6, "short", "en-US")).toBe("Sun");
  });
});
