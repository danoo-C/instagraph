import type { EChartsType } from "echarts";
import { CHART_TOKENS } from "@/charts/theme";
import type { ResolvedTheme } from "@/state/theme";
import { downloadFile, slugify } from "@/lib/utils";

/**
 * Export a chart as PNG at 2× with a themed background and a header containing the
 * title and date range (§11).
 */
export async function exportChartPng(
  chart: EChartsType,
  opts: { title: string; subtitle?: string; theme: ResolvedTheme; filename?: string },
): Promise<void> {
  const tk = CHART_TOKENS[opts.theme];
  const scale = 2;
  const url = chart.getDataURL({ type: "png", pixelRatio: scale, backgroundColor: tk.background });
  const img = await loadImage(url);

  const pad = 16 * scale;
  const header = (opts.subtitle ? 52 : 34) * scale;
  const canvas = document.createElement("canvas");
  canvas.width = img.width + pad * 2;
  canvas.height = img.height + header + pad;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  ctx.fillStyle = tk.background;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.textBaseline = "top";
  const font = "system-ui, -apple-system, Segoe UI, Roboto, sans-serif";
  ctx.fillStyle = tk.text;
  ctx.font = `600 ${16 * scale}px ${font}`;
  ctx.fillText(opts.title, pad, pad);
  if (opts.subtitle) {
    ctx.fillStyle = tk.muted;
    ctx.font = `${12 * scale}px ${font}`;
    ctx.fillText(opts.subtitle, pad, pad + 22 * scale);
  }
  ctx.drawImage(img, pad, header);

  downloadFile(`${opts.filename ?? slugify(opts.title)}.png`, canvas.toDataURL("image/png"));
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}
