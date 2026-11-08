import { Fragment, type ReactNode } from "react";

/** Split text into text nodes + <mark> for a search term. No HTML is ever injected. */
export function highlightText(text: string, term?: string): ReactNode {
  if (!term) return text;
  const lower = text.toLocaleLowerCase();
  const needle = term.toLocaleLowerCase();
  if (!needle || !lower.includes(needle)) return text;
  const out: ReactNode[] = [];
  let i = 0;
  let idx = lower.indexOf(needle, i);
  while (idx !== -1) {
    if (idx > i) out.push(text.slice(i, idx));
    out.push(
      <mark key={idx} className="rounded bg-yellow-200 px-0.5 text-inherit dark:bg-yellow-700/60">
        {text.slice(idx, idx + needle.length)}
      </mark>,
    );
    i = idx + needle.length;
    idx = lower.indexOf(needle, i);
  }
  if (i < text.length) out.push(text.slice(i));
  return out.map((n, k) => <Fragment key={k}>{n}</Fragment>);
}
