import { describe, expect, it } from "vitest";
import {
  addTerms,
  divergingRows,
  laughPairRows,
  MAX_TRACKED_TERMS,
  parseTermInput,
  withSavedTerms,
  type PersonTerms,
} from "@/features/tabs/words";

const person = (id: number, name = `P${id}`) => ({
  person_id: id,
  name,
  color_index: id,
  is_owner: id === 0,
});

describe("parseTermInput", () => {
  it("splits on commas outside regex mode", () => {
    expect(parseTermInput(" love, haha ,, ", "word")).toEqual(["love", "haha"]);
  });
  it("keeps the whole regex", () => {
    expect(parseTermInput("a{1,3}", "regex")).toEqual(["a{1,3}"]);
    expect(parseTermInput("   ", "regex")).toEqual([]);
  });
});

describe("addTerms", () => {
  it("dedups and caps", () => {
    expect(addTerms(["a"], ["a", "b"])).toEqual(["a", "b"]);
    const many = Array.from({ length: 20 }, (_, i) => `t${i}`);
    expect(addTerms([], many)).toHaveLength(MAX_TRACKED_TERMS);
  });
});

describe("divergingRows", () => {
  const byPerson: PersonTerms[] = [
    { person: person(0), terms: [{ term: "a", count: 3, score: 2 }] },
    {
      person: person(1),
      terms: [
        { term: "b", count: 5, score: 4 },
        { term: "c", count: 1, score: 1 },
      ],
    },
  ];
  it("mirrors the left person", () => {
    const rows = divergingRows(byPerson, 1, 0);
    expect(rows.map((r) => [r.term, r.value])).toEqual([
      ["a", -2],
      ["c", 1],
      ["b", 4],
    ]);
  });
  it("single person keeps scores", () => {
    expect(divergingRows(byPerson, 0).map((r) => r.value)).toEqual([2]);
  });
});

describe("laughPairRows", () => {
  it("labels and sorts", () => {
    const rows = laughPairRows([
      { laugher: person(0, "Me"), causer: person(1, "Jane"), count: 2 },
      { laugher: person(1, "Jane"), causer: person(0, "Me"), count: 5 },
      { laugher: person(1, "Jane"), causer: person(1, "Jane"), count: 0 },
    ]);
    expect(rows.map((r) => r.label)).toEqual(["Me → Jane", "Jane → Me"]);
  });
});

describe("withSavedTerms", () => {
  it("sets and removes a folder", () => {
    expect(withSavedTerms({ x: ["a"] }, "f", ["b"])).toEqual({ x: ["a"], f: ["b"] });
    expect(withSavedTerms({ x: ["a"], f: ["b"] }, "f", [])).toEqual({ x: ["a"] });
  });
});
