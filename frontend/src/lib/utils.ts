/** Join class names, skipping falsy values. */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/** Clamp `n` into [min, max]. */
export function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, n));
}

/** Stable 32-bit FNV-1a hash of a string (non-negative). */
export function hashString(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** Initials for an avatar: "Jane Doe" → "JD", "jane" → "JA". Grapheme-safe for emoji names. */
export function initials(name: string | null | undefined): string {
  const clean = (name ?? "").trim();
  if (!clean) return "?";
  const words = clean.split(/\s+/).filter(Boolean);
  const first = (w: string) => Array.from(w)[0] ?? "";
  if (words.length >= 2) return (first(words[0]!) + first(words[words.length - 1]!)).toUpperCase();
  return Array.from(clean).slice(0, 2).join("").toUpperCase();
}

/** Safe file name from a title: "Messages over time" → "messages-over-time". */
export function slugify(s: string): string {
  return (
    s
      .normalize("NFKD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "export"
  );
}

/** Format a Date as local YYYY-MM-DD. */
export function toIsoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Trigger a browser download of a Blob or data URL. */
export function downloadFile(filename: string, data: Blob | string): void {
  const url = typeof data === "string" ? data : URL.createObjectURL(data);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  if (typeof data !== "string") setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Escape text for HTML strings (ECharts tooltip/label `formatter`s render HTML).
 * Every user-derived value (names, titles, words, emojis, message text) must go through this.
 */
export function escapeHtml(s: string | number | null | undefined): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}
