import { hashString } from "@/lib/utils";

/**
 * Color-blind-friendly categorical palette (Okabe–Ito + Paul Tol extensions),
 * readable on both light and dark backgrounds. Order matters: index 0 is
 * conventionally the owner ("me"), then contacts by rank.
 */
export const PERSON_PALETTE = [
  "#0072B2", // blue
  "#E69F00", // orange
  "#009E73", // bluish green
  "#CC79A7", // reddish purple
  "#56B4E9", // sky blue
  "#D55E00", // vermillion
  "#AA4499", // purple
  "#44AA99", // teal
  "#999933", // olive
  "#882255", // wine
] as const;

/** Neutral color for aggregated series such as "others". */
export const OTHERS_COLOR = "#9CA3AF";

/** Sequential ramp (low → high) for heatmaps/calendars; color-blind safe (viridis-like). */
export const SEQUENTIAL_LIGHT = [
  "#f1f5f9",
  "#c7e9e0",
  "#7fcdbb",
  "#41b6c4",
  "#1d91c0",
  "#225ea8",
  "#0c2c84",
];
export const SEQUENTIAL_DARK = [
  "#1e293b",
  "#164e63",
  "#0e7490",
  "#0891b2",
  "#22d3ee",
  "#a5f3fc",
  "#ecfeff",
];

/** Diverging ramp (negative → positive), e.g. me vs them balance. */
export const DIVERGING = ["#D55E00", "#F4A582", "#E5E7EB", "#92C5DE", "#0072B2"];

/** Color for a palette index (wraps around). */
export function colorForIndex(index: number): string {
  const n = PERSON_PALETTE.length;
  return PERSON_PALETTE[((Math.trunc(index) % n) + n) % n]!;
}

export interface PersonColorSource {
  id?: string | number | null;
  color_index?: number | null;
}

/**
 * Stable color for a person, consistent across the whole app.
 * Prefers the backend-provided `color_index`; falls back to a hash of the id.
 * Accepts a person-like object, a numeric color index, or a string id.
 */
export function personColor(p: PersonColorSource | number | string | null | undefined): string {
  if (p == null) return OTHERS_COLOR;
  if (typeof p === "number") return colorForIndex(p);
  if (typeof p === "string") return colorForIndex(hashString(p));
  if (p.color_index != null) return colorForIndex(p.color_index);
  if (p.id != null) return colorForIndex(hashString(String(p.id)));
  return OTHERS_COLOR;
}

/** Pick black or white text for a given background hex color (WCAG relative luminance). */
export function readableTextColor(hex: string): "#000000" | "#ffffff" {
  const m = hex.replace("#", "");
  const full = m.length === 3 ? m.replace(/./g, (c) => c + c) : m;
  const [r, g, b] = [0, 2, 4].map((i) => {
    const c = parseInt(full.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lum > 0.4 ? "#000000" : "#ffffff";
}
