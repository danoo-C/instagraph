import { describe, expect, it } from "vitest";
import { apiTileValue, personByName, weekdayForRow } from "@/features/tabs/callsHelpers";

describe("callsHelpers", () => {
  it("formats tile values by unit", () => {
    expect(apiTileValue({ value: null, unit: "count" })).toBe("—");
    expect(apiTileValue({ value: 0.5, unit: "ratio" })).toMatch(/50/);
    expect(apiTileValue({ value: 90_000, unit: "ms" })).toBe("1 min 30 s");
    expect(apiTileValue({ value: "x", unit: "text" })).toBe("x");
    expect(apiTileValue({ value: true, unit: null })).toBe("Yes");
  });
  it("finds people by name and maps weekday rows", () => {
    const p = { person_id: 3, name: "Ann", color_index: 1, is_owner: false };
    expect(personByName([p], "Ann")).toBe(p);
    expect(personByName([p], "Bob")).toBeUndefined();
    expect(weekdayForRow(0)).toBe(1);
  });
});
