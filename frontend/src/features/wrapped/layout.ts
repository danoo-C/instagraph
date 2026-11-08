import type { ListItem, Slide, SlideBlock } from "@/features/wrapped/model";

/** Canvas size of a story slide (9:16). */
export const SLIDE_W = 1080;
export const SLIDE_H = 1920;
export const PAD = 96;
export const FONT_FAMILY =
  'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Color Emoji", "Apple Color Emoji", "Segoe UI Emoji", sans-serif';

/** Measures text width for a CSS font string (ctx.measureText in the browser). */
export type Measure = (text: string, font: string) => number;

export function font(size: number, weight = 400, style = ""): string {
  return `${style ? `${style} ` : ""}${weight} ${Math.round(size)}px ${FONT_FAMILY}`;
}

export type DrawOp =
  | {
      op: "text";
      text: string;
      x: number;
      y: number;
      font: string;
      color: string;
      align: "left" | "center" | "right";
    }
  | {
      op: "rect";
      x: number;
      y: number;
      w: number;
      h: number;
      r: number;
      color: string;
      alpha?: number;
    }
  | { op: "circle"; x: number; y: number; r: number; color: string };

// ------------------------------------------------------------------ text helpers

/** Shorten `text` with "…" so it fits `maxWidth`. */
export function truncateText(text: string, maxWidth: number, f: string, measure: Measure): string {
  if (measure(text, f) <= maxWidth) return text;
  const chars = Array.from(text);
  let lo = 0;
  let hi = chars.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (measure(`${chars.slice(0, mid).join("").trimEnd()}…`, f) <= maxWidth) lo = mid;
    else hi = mid - 1;
  }
  return `${chars.slice(0, lo).join("").trimEnd()}…`;
}

/** Word-wrap into lines no wider than `maxWidth`; long words are broken; ellipsis past `maxLines`. */
export function wrapText(
  text: string,
  maxWidth: number,
  f: string,
  measure: Measure,
  maxLines = Infinity,
): string[] {
  const lines: string[] = [];
  for (const paragraph of text.split("\n")) {
    const words = paragraph.split(/\s+/).filter(Boolean);
    let line = "";
    for (const word of words) {
      const candidate = line ? `${line} ${word}` : word;
      if (measure(candidate, f) <= maxWidth) {
        line = candidate;
        continue;
      }
      if (line) lines.push(line);
      // Break words that are wider than a full line.
      let rest = word;
      while (measure(rest, f) > maxWidth) {
        const chars = Array.from(rest);
        let n = 1;
        while (n < chars.length && measure(chars.slice(0, n + 1).join(""), f) <= maxWidth) n++;
        lines.push(chars.slice(0, n).join(""));
        rest = chars.slice(n).join("");
      }
      line = rest;
    }
    if (line || words.length === 0) lines.push(line);
  }
  if (lines.length > maxLines) {
    const kept = lines.slice(0, maxLines);
    kept[maxLines - 1] = truncateText(
      `${kept[maxLines - 1]} ${lines[maxLines]}`,
      maxWidth,
      f,
      measure,
    );
    if (!kept[maxLines - 1]!.endsWith("…")) kept[maxLines - 1] += "…";
    return kept;
  }
  return lines;
}

/** Largest font size (step 2px) in [min, max] at which `text` fits on one line. */
export function fitFontSize(
  text: string,
  maxWidth: number,
  maxSize: number,
  minSize: number,
  measure: Measure,
  weight = 800,
): number {
  for (let s = maxSize; s > minSize; s -= 2) {
    if (measure(text, font(s, weight)) <= maxWidth) return s;
  }
  return minSize;
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Bar chart rectangles (bottom-aligned, scaled to the max; zero bars get a 4px stub). */
export function barLayout(values: number[], box: Rect, gap = 12): Rect[] {
  const n = values.length;
  if (n === 0) return [];
  const w = Math.max(1, (box.w - gap * (n - 1)) / n);
  const max = Math.max(0, ...values);
  return values.map((v, i) => {
    const h = max > 0 ? Math.max(4, (Math.max(0, v) / max) * box.h) : 4;
    return { x: box.x + i * (w + gap), y: box.y + box.h - h, w, h };
  });
}

/** Lay chips out in rows: returns each chip's rect, wrapping at `maxWidth`. */
export function chipLayout(
  widths: number[],
  maxWidth: number,
  height: number,
  gap = 20,
): Array<Rect & { row: number }> {
  const out: Array<Rect & { row: number }> = [];
  let x = 0;
  let row = 0;
  for (const w0 of widths) {
    const w = Math.min(w0, maxWidth);
    if (x > 0 && x + w > maxWidth) {
      row++;
      x = 0;
    }
    out.push({ x, y: row * (height + gap), w, h: height, row });
    x += w + gap;
  }
  return out;
}

// ------------------------------------------------------------------ block layout

const TEXT_SIZES = { lg: 64, md: 48, sm: 38 } as const;
const LIST_ROW = 112;
const BARS_H = 340;
const CHIP_H = 88;
const BLOCK_GAP = 56;

interface Laid {
  height: number;
  /** Ops relative to (PAD, top of block). */
  ops: DrawOp[];
}

function layoutBlock(block: SlideBlock, slide: Slide, measure: Measure, width: number): Laid {
  const { fg, muted, accent } = slide.theme;
  const cx = width / 2;
  switch (block.kind) {
    case "big": {
      const size = fitFontSize(block.text, width, 260, 72, measure);
      return {
        height: size * 1.1,
        ops: [
          {
            op: "text",
            text: block.text,
            x: cx,
            y: size * 0.88,
            font: font(size, 800),
            color: fg,
            align: "center",
          },
        ],
      };
    }
    case "text": {
      const size = TEXT_SIZES[block.size ?? "md"];
      const f = font(size, block.size === "lg" ? 700 : 500);
      const lines = wrapText(block.text, width, f, measure, 8);
      const lh = size * 1.28;
      return {
        height: lines.length * lh,
        ops: lines.map((text, i) => ({
          op: "text" as const,
          text,
          x: cx,
          y: i * lh + size,
          font: f,
          color: block.muted ? muted : fg,
          align: "center" as const,
        })),
      };
    }
    case "list":
      return layoutList(block.items, slide, measure, width);
    case "bars": {
      const labelH = 48;
      const rects = barLayout(block.values, { x: 0, y: 0, w: width, h: BARS_H - labelH }, 14);
      const ops: DrawOp[] = [];
      rects.forEach((r, i) => {
        ops.push({ op: "rect", ...r, r: Math.min(10, r.w / 2), color: accent });
        const label = block.labels[i];
        if (label)
          ops.push({
            op: "text",
            text: label,
            x: r.x + r.w / 2,
            y: BARS_H - 8,
            font: font(28, 600),
            color: muted,
            align: "center",
          });
      });
      return { height: BARS_H, ops };
    }
    case "quote": {
      const f = font(52, 500, "italic");
      const inner = width - 96;
      const lines = wrapText(`“${block.text}”`, inner, f, measure, 7);
      const lh = 68;
      const metaF = font(34, 500);
      const meta = truncateText(block.meta, inner, metaF, measure);
      const h = 48 + lines.length * lh + 24 + 44 + 40;
      const ops: DrawOp[] = [
        { op: "rect", x: 0, y: 0, w: width, h, r: 40, color: "#ffffff", alpha: 0.14 },
      ];
      lines.forEach((text, i) =>
        ops.push({
          op: "text",
          text,
          x: cx,
          y: 48 + i * lh + 52,
          font: f,
          color: fg,
          align: "center",
        }),
      );
      ops.push({
        op: "text",
        text: meta,
        x: cx,
        y: 48 + lines.length * lh + 24 + 34,
        font: metaF,
        color: muted,
        align: "center",
      });
      return { height: h, ops };
    }
    case "chips": {
      const f = font(44, 600);
      const widths = block.items.map((t) => measure(t, f) + 64);
      const rects = chipLayout(widths, width, CHIP_H);
      const rows = rects.length ? rects[rects.length - 1]!.row + 1 : 0;
      const ops: DrawOp[] = [];
      // Center each row.
      for (let row = 0; row < rows; row++) {
        const inRow = rects.map((r, i) => ({ r, i })).filter((x) => x.r.row === row);
        const last = inRow[inRow.length - 1]!.r;
        const offset = (width - (last.x + last.w)) / 2;
        for (const { r, i } of inRow) {
          ops.push({
            op: "rect",
            x: r.x + offset,
            y: r.y,
            w: r.w,
            h: r.h,
            r: r.h / 2,
            color: "#ffffff",
            alpha: 0.18,
          });
          ops.push({
            op: "text",
            text: truncateText(block.items[i]!, r.w - 64, f, measure),
            x: r.x + offset + r.w / 2,
            y: r.y + r.h / 2 + 16,
            font: f,
            color: fg,
            align: "center",
          });
        }
      }
      return { height: rows ? rows * CHIP_H + (rows - 1) * 20 : 0, ops };
    }
  }
}

function layoutList(items: ListItem[], slide: Slide, measure: Measure, width: number): Laid {
  const { fg, muted } = slide.theme;
  const ops: DrawOp[] = [];
  const labelF = font(46, 600);
  const valueF = font(40, 500);
  const iconF = font(52, 400);
  items.forEach((item, i) => {
    const y = i * LIST_ROW;
    const mid = y + LIST_ROW / 2;
    let x = 0;
    if (item.icon) {
      ops.push({
        op: "text",
        text: item.icon,
        x: 36,
        y: mid + 18,
        font: iconF,
        color: fg,
        align: "center",
      });
      x = 96;
    } else if (item.rank != null) {
      ops.push({ op: "circle", x: 36, y: mid, r: 36, color: item.color ?? "#ffffff" });
      ops.push({
        op: "text",
        text: String(item.rank),
        x: 36,
        y: mid + 14,
        font: font(38, 800),
        color: "#111111",
        align: "center",
      });
      x = 96;
    }
    const valueW = item.value ? measure(item.value, valueF) + 24 : 0;
    ops.push({
      op: "text",
      text: truncateText(item.label, width - x - valueW, labelF, measure),
      x,
      y: mid + 16,
      font: labelF,
      color: fg,
      align: "left",
    });
    if (item.value)
      ops.push({
        op: "text",
        text: item.value,
        x: width,
        y: mid + 14,
        font: valueF,
        color: muted,
        align: "right",
      });
  });
  return { height: items.length * LIST_ROW, ops };
}

function shift(ops: DrawOp[], dx: number, dy: number): DrawOp[] {
  return ops.map((o) => ({ ...o, x: o.x + dx, y: o.y + dy }));
}

/**
 * Full slide layout in canvas pixels (1080×1920): kicker + title at the top, blocks
 * vertically centered in the remaining space, footer at the bottom. Pure — the browser
 * passes `ctx.measureText`, tests pass a fake.
 */
export function layoutSlide(slide: Slide, measure: Measure, w = SLIDE_W, h = SLIDE_H): DrawOp[] {
  const width = w - PAD * 2;
  const ops: DrawOp[] = [];
  const { fg, muted } = slide.theme;
  const kickerF = font(40, 700);
  ops.push({
    op: "text",
    text: truncateText(slide.kicker.toUpperCase(), width, kickerF, measure),
    x: w / 2,
    y: 230,
    font: kickerF,
    color: muted,
    align: "center",
  });
  const titleSize = 76;
  const titleF = font(titleSize, 800);
  const titleLines = wrapText(slide.title, width, titleF, measure, 3);
  titleLines.forEach((text, i) =>
    ops.push({
      op: "text",
      text,
      x: w / 2,
      y: 330 + i * titleSize * 1.15,
      font: titleF,
      color: fg,
      align: "center",
    }),
  );
  const top = 330 + titleLines.length * titleSize * 1.15 + 40;
  const bottom = h - 220;

  const laid = slide.blocks.map((b) => layoutBlock(b, slide, measure, width));
  const total = laid.reduce((s, l) => s + l.height, 0) + BLOCK_GAP * Math.max(0, laid.length - 1);
  let y = Math.max(top, top + (bottom - top - total) / 2);
  for (const l of laid) {
    ops.push(...shift(l.ops, PAD, y));
    y += l.height + BLOCK_GAP;
  }

  const footF = font(34, 600);
  ops.push({
    op: "text",
    text: slide.footer,
    x: w / 2,
    y: h - 110,
    font: footF,
    color: muted,
    align: "center",
  });
  return ops;
}
