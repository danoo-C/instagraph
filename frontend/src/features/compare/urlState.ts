/** Compare page URL state (§5.8): `?chats=1,2,3` or `?people=4,5`. Pure helpers. */

export type CompareMode = "chats" | "people";

export interface CompareState {
  mode: CompareMode;
  ids: number[];
}

export const MIN_ITEMS = 2;
export const MAX_ITEMS = 4;

/** "3, 1,x,3,7" → [3, 1, 7] (valid non-negative ints, deduped, at most MAX_ITEMS). */
export function parseIds(raw: string | null | undefined): number[] {
  if (!raw) return [];
  const out: number[] = [];
  for (const part of raw.split(",")) {
    const s = part.trim();
    if (!/^\d+$/.test(s)) continue;
    const n = Number(s);
    if (!out.includes(n)) out.push(n);
    if (out.length >= MAX_ITEMS) break;
  }
  return out;
}

/** Read the state; `chats` wins when both params are present and non-empty. */
export function parseCompareState(params: URLSearchParams): CompareState {
  const chats = parseIds(params.get("chats"));
  const people = parseIds(params.get("people"));
  if (chats.length) return { mode: "chats", ids: chats };
  if (people.length) return { mode: "people", ids: people };
  if (params.has("people") && !params.has("chats")) return { mode: "people", ids: [] };
  return { mode: "chats", ids: [] };
}

/** Write the state into a copy of `params`, keeping every other param (global filters). */
export function writeCompareState(params: URLSearchParams, state: CompareState): URLSearchParams {
  const next = new URLSearchParams(params);
  next.delete("chats");
  next.delete("people");
  next.set(state.mode, state.ids.slice(0, MAX_ITEMS).join(","));
  return next;
}

export function addId(state: CompareState, id: number): CompareState {
  if (state.ids.includes(id) || state.ids.length >= MAX_ITEMS) return state;
  return { ...state, ids: [...state.ids, id] };
}

export function removeId(state: CompareState, id: number): CompareState {
  return { ...state, ids: state.ids.filter((x) => x !== id) };
}

/** Switching mode clears the selection (ids mean different things). */
export function switchMode(state: CompareState, mode: CompareMode): CompareState {
  return state.mode === mode ? state : { mode, ids: [] };
}

export function canCompare(state: CompareState): boolean {
  return state.ids.length >= MIN_ITEMS && state.ids.length <= MAX_ITEMS;
}

/** Query params for `GET /api/compare`. */
export function compareQuery(state: CompareState): { chats?: string; people?: string } {
  return state.mode === "chats" ? { chats: state.ids.join(",") } : { people: state.ids.join(",") };
}

/**
 * Distinct colors for compared items: keep each item's own color index unless it collides
 * with an earlier item, then take the first unused palette slot.
 */
export function distinctColorIndexes(
  indexes: Array<number | null | undefined>,
  paletteSize: number,
): number[] {
  const used = new Set<number>();
  return indexes.map((raw) => {
    let ci = raw == null ? -1 : ((raw % paletteSize) + paletteSize) % paletteSize;
    if (ci < 0 || used.has(ci)) {
      ci = 0;
      while (used.has(ci) && ci < paletteSize) ci++;
    }
    used.add(ci);
    return ci;
  });
}
