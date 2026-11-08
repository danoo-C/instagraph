import { describe, expect, it } from "vitest";
import { bestIndex, formatScalar } from "@/features/people/formatScalar";

describe("formatScalar / bestIndex", () => {
  it("formats by unit", () => {
    expect(formatScalar(90000, "ms")).toBe("1 min 30 s");
    expect(formatScalar(null, "count")).toBe("—");
    expect(formatScalar("x", "text")).toBe("x");
  });
  it("picks the best value", () => {
    expect(bestIndex([1, 3, 2])).toBe(1);
    expect(bestIndex([1, 3, 2], true)).toBe(0);
    expect(bestIndex([2, 2])).toBeNull();
    expect(bestIndex([null, 5])).toBeNull();
  });
});
