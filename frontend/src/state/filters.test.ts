import { describe, expect, it } from "vitest";
import {
  defaultFilters,
  detectPreset,
  filtersToQuery,
  parseFilters,
  presetRange,
  writeFilters,
} from "@/state/filters";

const TZ = "Europe/Bratislava";

describe("filters URL round-trip", () => {
  it("defaults produce an empty query string", () => {
    expect(writeFilters(defaultFilters(TZ), undefined, TZ).toString()).toBe("");
    expect(parseFilters(new URLSearchParams(), TZ)).toEqual(defaultFilters(TZ));
  });

  it("round-trips non-default values and keeps foreign params", () => {
    const f = {
      ...defaultFilters(TZ),
      start: "2023-01-01",
      end: "2023-12-31",
      tz: "UTC",
      include_requests: true,
      include_archived: false,
      people: ["p1", "p2"],
    };
    const qs = writeFilters(f, new URLSearchParams("q=hello"), TZ);
    expect(qs.get("q")).toBe("hello");
    expect(qs.get("include_groups")).toBeNull();
    expect(parseFilters(qs, TZ)).toEqual(f);
  });

  it("ignores invalid values", () => {
    const f = parseFilters(new URLSearchParams("start=yesterday&include_groups=maybe"), TZ);
    expect(f.start).toBeNull();
    expect(f.include_groups).toBe(true);
  });

  it("serializes to backend query params", () => {
    expect(filtersToQuery({ ...defaultFilters(TZ), people: ["a", "b"] })).toEqual({
      tz: TZ,
      include_groups: "true",
      include_requests: "false",
      include_archived: "true",
      exclude_system: "true",
      people: "a,b",
    });
  });
});

describe("presets", () => {
  const today = new Date(2024, 5, 15);
  it("computes and detects presets", () => {
    expect(presetRange({ kind: "last30d" }, today)).toEqual({
      start: "2024-05-17",
      end: "2024-06-15",
    });
    expect(presetRange({ kind: "last12m" }, today)).toEqual({
      start: "2023-06-16",
      end: "2024-06-15",
    });
    expect(detectPreset({ start: "2022-01-01", end: "2022-12-31" }, today)).toEqual({
      kind: "year",
      year: 2022,
    });
    expect(detectPreset({ start: "2024-05-17", end: "2024-06-15" }, today)).toEqual({
      kind: "last30d",
    });
    expect(detectPreset({ start: "2024-05-01", end: null }, today)).toEqual({ kind: "custom" });
    expect(detectPreset({ start: null, end: null }, today)).toEqual({ kind: "all" });
  });
});
