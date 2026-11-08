/** Pure helpers for the Media & Links tab gallery. */

/** Columns and cell size for a grid of square cells that fills `width`. */
export function galleryLayout(
  width: number,
  minCell = 120,
  gap = 8,
): { columns: number; cell: number } {
  const w = Math.max(0, width);
  const columns = Math.max(1, Math.floor((w + gap) / (minCell + gap)));
  const cell = Math.max(40, Math.floor((w - gap * (columns - 1)) / columns));
  return { columns, cell };
}

/** Split a flat list into rows of `columns` items. */
export function chunkRows<T>(items: readonly T[], columns: number): T[][] {
  const rows: T[][] = [];
  const n = Math.max(1, columns);
  for (let i = 0; i < items.length; i += n) rows.push(items.slice(i, i + n));
  return rows;
}

/** Media kinds labels for the counts chart and gallery. */
export const MEDIA_KIND_LABEL: Record<string, string> = {
  photo: "Photo",
  video: "Video",
  audio: "Voice note",
  gif: "GIF",
  sticker: "Sticker",
};
