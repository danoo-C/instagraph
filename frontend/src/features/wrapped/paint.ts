import { layoutSlide, SLIDE_H, SLIDE_W, type DrawOp } from "@/features/wrapped/layout";
import type { Slide } from "@/features/wrapped/model";

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  const rr = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

function paintOp(ctx: CanvasRenderingContext2D, o: DrawOp) {
  ctx.save();
  switch (o.op) {
    case "text":
      ctx.font = o.font;
      ctx.fillStyle = o.color;
      ctx.textAlign = o.align;
      ctx.textBaseline = "alphabetic";
      ctx.fillText(o.text, o.x, o.y);
      break;
    case "rect":
      ctx.globalAlpha = o.alpha ?? 1;
      ctx.fillStyle = o.color;
      roundRect(ctx, o.x, o.y, o.w, o.h, o.r);
      ctx.fill();
      break;
    case "circle":
      ctx.fillStyle = o.color;
      ctx.beginPath();
      ctx.arc(o.x, o.y, o.r, 0, Math.PI * 2);
      ctx.fill();
      break;
  }
  ctx.restore();
}

/** Draw a slide (background gradient + laid-out ops) onto a 1080×1920 canvas. */
export function paintSlide(canvas: HTMLCanvasElement, slide: Slide): void {
  canvas.width = SLIDE_W;
  canvas.height = SLIDE_H;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const g = ctx.createLinearGradient(0, 0, SLIDE_W, SLIDE_H);
  g.addColorStop(0, slide.theme.from);
  g.addColorStop(1, slide.theme.to);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, SLIDE_W, SLIDE_H);
  // Soft decorative circles.
  ctx.save();
  ctx.globalAlpha = 0.08;
  ctx.fillStyle = "#ffffff";
  ctx.beginPath();
  ctx.arc(SLIDE_W * 0.9, SLIDE_H * 0.12, 320, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.arc(SLIDE_W * 0.05, SLIDE_H * 0.92, 260, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
  const measure = (text: string, f: string) => {
    ctx.font = f;
    return ctx.measureText(text).width;
  };
  for (const o of layoutSlide(slide, measure)) paintOp(ctx, o);
}

/** Render a slide to a PNG blob (1080×1920). */
export function slideToPng(slide: Slide): Promise<Blob | null> {
  const canvas = document.createElement("canvas");
  paintSlide(canvas, slide);
  return new Promise((resolve) => canvas.toBlob((b) => resolve(b), "image/png"));
}
