import type { components } from "@/api/schema";

/** Pure helpers for the Words tab (term chips, diverging distinctive words, laugh pairs). */

type S = components["schemas"];
export type PersonTerms = S["PersonTerms"];
export type LaughPair = S["LaughPair"];
export type TermMode = "plain" | "word" | "regex";

/** Max terms tracked at once (backend MAX_TERMS guard is larger; keep requests few). */
export const MAX_TRACKED_TERMS = 10;

/**
 * Split raw chip input into terms. In plain/word mode commas separate terms (the backend
 * also splits on commas there); in regex mode the whole input is one pattern.
 */
export function parseTermInput(input: string, mode: TermMode): string[] {
  const parts = mode === "regex" ? [input] : input.split(",");
  return parts.map((p) => p.trim()).filter(Boolean);
}

/** Add terms to a chip list (dedup, keep order, cap at MAX_TRACKED_TERMS). */
export function addTerms(current: readonly string[], added: readonly string[]): string[] {
  const out = [...current];
  for (const t of added) {
    if (!out.includes(t) && out.length < MAX_TRACKED_TERMS) out.push(t);
  }
  return out;
}

export interface DivergingRow {
  term: string;
  /** Signed score: negative = left person, positive = right person. */
  value: number;
  personId: number;
  count: number;
}

/**
 * Diverging bar rows. With two people (`leftId`, `rightId`) the left person's distinctive
 * words get negative values and the right person's positive ones; with only `rightId`
 * the person's own scores are kept as-is. Sorted ascending by value (bottom → top in ECharts).
 */
export function divergingRows(
  byPerson: readonly PersonTerms[],
  rightId: number,
  leftId?: number | null,
  limit = 25,
): DivergingRow[] {
  const rows: DivergingRow[] = [];
  const take = (pid: number, sign: 1 | -1) => {
    const p = byPerson.find((x) => x.person.person_id === pid);
    for (const t of (p?.terms ?? []).slice(0, limit)) {
      rows.push({
        term: t.term,
        value: sign * (t.score ?? t.count),
        personId: pid,
        count: t.count,
      });
    }
  };
  take(rightId, 1);
  if (leftId != null && leftId !== rightId) take(leftId, -1);
  return rows.sort((a, b) => a.value - b.value);
}

/** Laugh pairs as bar categories "A → B" (causer made laugher laugh), largest first. */
export function laughPairRows(
  pairs: readonly LaughPair[],
  limit = 15,
): { label: string; count: number; pair: LaughPair }[] {
  return [...pairs]
    .filter((p) => p.count > 0)
    .sort((a, b) => b.count - a.count)
    .slice(0, limit)
    .map((p) => ({ label: `${p.causer.name} → ${p.laugher.name}`, count: p.count, pair: p }));
}

/** New `custom_terms` map with this chat's folder set (or removed when `terms` is empty). */
export function withSavedTerms(
  existing: Record<string, string[]> | null | undefined,
  folder: string,
  terms: readonly string[],
): Record<string, string[]> {
  const next: Record<string, string[]> = { ...(existing ?? {}) };
  if (terms.length) next[folder] = [...terms];
  else delete next[folder];
  return next;
}
