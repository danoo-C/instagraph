import type { ReactNode } from "react";

/** Temporary page body used by placeholder pages. */
export function PagePlaceholder({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="page">
      <h1 className="page-title">{title}</h1>
      {children ?? <p className="text-sm text-muted">Coming soon.</p>}
    </div>
  );
}
