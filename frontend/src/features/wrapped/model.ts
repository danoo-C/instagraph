import type { MessageQuery } from "@/lib/drilldown";

/** Data-only description of one Wrapped slide; rendered to canvas by `layout.ts`. */
export interface SlideTheme {
  from: string;
  to: string;
  fg: string;
  muted: string;
  accent: string;
}

export interface ListItem {
  rank?: number;
  label: string;
  value?: string;
  color?: string;
  /** Leading emoji / symbol instead of a rank number. */
  icon?: string;
}

export type SlideBlock =
  | { kind: "big"; text: string }
  | { kind: "text"; text: string; size?: "lg" | "md" | "sm"; muted?: boolean }
  | { kind: "list"; items: ListItem[] }
  | { kind: "bars"; values: number[]; labels: string[] }
  | { kind: "quote"; text: string; meta: string }
  | { kind: "chips"; items: string[] };

export interface Slide {
  key: string;
  kicker: string;
  title: string;
  blocks: SlideBlock[];
  footer: string;
  theme: SlideTheme;
  /** Opens the messages behind the slide. */
  drilldown?: MessageQuery;
}
