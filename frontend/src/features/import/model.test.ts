import { describe, expect, it } from "vitest";
import {
  describeImportError,
  formatBytes,
  overallProgress,
  stepIndex,
  zipFilesOnly,
} from "@/features/import/model";
import { durationRows, normalizeReport } from "@/features/import/report";

describe("import stages", () => {
  it("maps backend stages to the four UI steps", () => {
    expect(stepIndex("discovery")).toBe(0);
    expect(stepIndex("parse")).toBe(1);
    expect(stepIndex("merge")).toBe(2);
    expect(stepIndex("persist")).toBe(3);
    expect(stepIndex("done")).toBe(4);
  });
  it("progress is monotonic across stages and 1 when done", () => {
    const p = (stage: string, done = 0, total = 0) =>
      overallProgress({ stage, done, total, status: "running" });
    expect(p("parse", 5, 10)).toBeGreaterThan(p("parse", 0, 10));
    expect(p("merge")).toBeGreaterThan(p("parse", 10, 10) - 1e-9);
    expect(overallProgress({ stage: "persist", done: 0, total: 1, status: "done" })).toBe(1);
  });
});

describe("import helpers", () => {
  it("describes known error codes and falls back", () => {
    expect(describeImportError("html_export").title).toMatch(/HTML/);
    expect(describeImportError("weird").title).toBe("Import failed");
  });
  it("formats bytes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 ** 3)).toBe("5.0 GB");
  });
  it("filters zip files", () => {
    const { zips, rejected } = zipFilesOnly([{ name: "a.ZIP" }, { name: "b.json" }]);
    expect(zips).toEqual([{ name: "a.ZIP" }]);
    expect(rejected).toEqual([{ name: "b.json" }]);
  });
  it("orders durations by pipeline stage", () => {
    expect(
      durationRows({ persist: 1, parse: 2, zzz: 3, discovery: 4 }).map((r) => r.stage),
    ).toEqual(["discovery", "parse", "persist", "zzz"]);
  });
  it("fills report defaults", () => {
    const r = normalizeReport({ dataset_version: 1 } as Parameters<typeof normalizeReport>[0]);
    expect(r.unknown_keys).toEqual([]);
    expect(r.owner.method).toBe("none");
    expect(r.locales.detected).toEqual({});
  });
});
