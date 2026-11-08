import type { components } from "@/api/schema";
import { personRefColor } from "@/features/tabs/charts";

type S = components["schemas"];
export type InteractionMatrix = S["InteractionMatrix"];
export type MemberTimeline = S["MemberTimeline"];

export interface ChordNode {
  id: string;
  name: string;
  value: number;
  symbolSize: number;
  itemStyle: { color: string };
}
export interface ChordLinkOut {
  source: string;
  target: string;
  value: number;
  lineStyle: { width: number; color: string; curveness: number; opacity: number };
}

/**
 * Interaction matrix → ECharts graph (circular layout) nodes and links.
 * Node size scales with `sizeBy[person_id]` (e.g. messages), edge width with the value.
 */
export function chordGraph(
  m: InteractionMatrix,
  sizeBy: Record<number, number> = {},
  { minNode = 12, maxNode = 48, maxWidth = 10 } = {},
): { nodes: ChordNode[]; links: ChordLinkOut[] } {
  const sizes = m.people.map((p) => sizeBy[p.person_id] ?? 0);
  const maxSize = Math.max(1, ...sizes);
  const nodes: ChordNode[] = m.people.map((p, i) => ({
    id: String(p.person_id),
    name: p.name,
    value: sizes[i] ?? 0,
    symbolSize: minNode + ((maxNode - minNode) * Math.sqrt(sizes[i] ?? 0)) / Math.sqrt(maxSize),
    itemStyle: { color: personRefColor(p) },
  }));
  const colorOf = new Map(m.people.map((p) => [p.person_id, personRefColor(p)]));
  const links0 = m.links.filter((l) => l.value > 0 && l.source !== l.target);
  const maxV = Math.max(1, ...links0.map((l) => l.value));
  const links = links0.map((l) => ({
    source: String(l.source),
    target: String(l.target),
    value: l.value,
    lineStyle: {
      width: 1 + ((maxWidth - 1) * l.value) / maxV,
      color: colorOf.get(l.source) ?? "#999",
      curveness: 0.3,
      opacity: 0.7,
    },
  }));
  return { nodes, links };
}

/** One Gantt bar: `[memberIndex, startMs, endMs]`. */
export type GanttBar = [number, number, number];

/**
 * Membership intervals → Gantt bars. `null` start = span start, `null` end = span end.
 * Members without any interval are treated as members for the whole span.
 */
export function membershipBars(
  timeline: MemberTimeline[],
  spanStart: number,
  spanEnd: number,
): GanttBar[] {
  const bars: GanttBar[] = [];
  timeline.forEach((m, i) => {
    const intervals = m.intervals.length ? m.intervals : [{ start: null, end: null }];
    for (const iv of intervals) {
      const s = iv.start ? Date.parse(iv.start) : spanStart;
      const e = iv.end ? Date.parse(iv.end) : spanEnd;
      bars.push([i, Math.min(s, e), Math.max(s, e)]);
    }
  });
  return bars;
}
