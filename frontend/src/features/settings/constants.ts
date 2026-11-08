/** Mirrors backend DEFAULT_LAUGHTER_PATTERNS (store/app_settings.py). */
export const DEFAULT_LAUGHTER_PATTERNS = [
  "\\b(?:a?ha){2,}h?\\b",
  "\\b(?:e?he){2,}h?\\b",
  "\\bx+d+\\b",
  "\\blol+\\b",
  "\\blmao+\\b",
  "\\brofl\\b",
  "😂",
  "🤣",
  "💀",
  "😆",
];

/** `/api/export/{scope}.json` link with the current global filters. */
export function exportHref(
  scope: string,
  filters: Record<string, string | undefined>,
  includeMessages: boolean,
): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) if (v != null && v !== "") qs.set(k, v);
  if (includeMessages) qs.set("include_messages", "true");
  const s = qs.toString();
  return `/api/export/${scope}.json${s ? `?${s}` : ""}`;
}
