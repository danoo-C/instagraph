import { describe, expect, it } from "vitest";
import { escapeCsvCell, recordsToTable, toCsv } from "@/charts/csv";

describe("csv", () => {
  it("escapes quotes, commas, newlines", () => {
    expect(escapeCsvCell('say "hi", ok')).toBe('"say ""hi"", ok"');
    expect(escapeCsvCell("a\nb")).toBe('"a\nb"');
    expect(escapeCsvCell(null)).toBe("");
    expect(escapeCsvCell(3.5)).toBe("3.5");
    expect(escapeCsvCell(-3)).toBe("-3");
  });

  it("neutralizes formula injection in strings", () => {
    expect(escapeCsvCell("=SUM(A1)")).toBe("'=SUM(A1)");
    expect(escapeCsvCell("@me")).toBe("'@me");
  });

  it("builds a table", () => {
    expect(
      toCsv({
        headers: ["name", "count"],
        rows: [
          ["Ján", 3],
          ["Bo, b", 1],
        ],
      }),
    ).toBe('name,count\r\nJán,3\r\n"Bo, b",1');
  });

  it("converts records with renamed columns", () => {
    const t = recordsToTable([{ a: 1, b: "x", c: true }], ["b", { key: "a", header: "A" }]);
    expect(t).toEqual({ headers: ["b", "A"], rows: [["x", 1]] });
  });
});
