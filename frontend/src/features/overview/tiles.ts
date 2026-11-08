import type { ApiTile } from "@/features/overview/types";
import { formatDate, formatDateTime, type ValueFormat } from "@/lib/format";

/** Backend tile unit → shared Tile value + format (durations: ms → s). */
export function tileDisplay(t: ApiTile): { value: number | string | null; format: ValueFormat } {
  const v = t.value;
  if (v == null || typeof v === "boolean")
    return { value: v == null ? null : String(v), format: "text" };
  switch (t.unit) {
    case "ratio":
      return { value: Number(v), format: "percent" };
    case "ms":
      return { value: Number(v) / 1000, format: "duration" };
    case "s":
      return { value: Number(v), format: "duration" };
    case "date":
      return { value: formatDate(String(v)), format: "text" };
    case "datetime":
      return { value: formatDateTime(String(v)), format: "text" };
    case "text":
    case "emoji":
      return { value: String(v), format: "text" };
    default:
      return { value: typeof v === "number" ? v : String(v), format: "number" };
  }
}

/** Tiles whose detail is an ISO date (or range) we can format nicely. */
export function formatDetail(detail: string | null | undefined): string | undefined {
  if (!detail) return undefined;
  return detail.replace(/\b\d{4}-\d{2}-\d{2}\b/g, (d) => formatDate(d));
}
