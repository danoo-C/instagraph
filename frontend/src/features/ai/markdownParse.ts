/** Parsing for the AI answer renderer (`markdown.tsx`). Pure functions, unit-tested. */

export type Inline =
  | { kind: "text"; text: string }
  | { kind: "bold" | "italic" | "code"; text: string }
  | { kind: "msg" | "chat"; id: number };

const INLINE_RE =
  /\[\[(msg|chat):(\d+)\]\]|\*\*([^*\n]+)\*\*|`([^`\n]+)`|\*([^*\s][^*\n]*?)\*|(?<![\w])_([^_\s][^_\n]*?)_(?![\w])/g;

/** Split one line into inline tokens. */
export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  for (const m of text.matchAll(INLINE_RE)) {
    const start = m.index ?? 0;
    if (start > last) out.push({ kind: "text", text: text.slice(last, start) });
    if (m[1]) out.push({ kind: m[1] as "msg" | "chat", id: Number(m[2]) });
    else if (m[3] != null) out.push({ kind: "bold", text: m[3] });
    else if (m[4] != null) out.push({ kind: "code", text: m[4] });
    else out.push({ kind: "italic", text: m[5] ?? m[6] ?? "" });
    last = start + m[0].length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}

export type Block =
  | { kind: "p" | "h"; lines: string[] }
  | { kind: "ul" | "ol"; items: string[] }
  | { kind: "code"; text: string };

const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;

/** Split an answer into blocks. */
export function parseBlocks(src: string): Block[] {
  const blocks: Block[] = [];
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? "";
    if (!line.trim()) {
      i++;
      continue;
    }
    if (line.trimStart().startsWith("```")) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !(lines[i] ?? "").trimStart().startsWith("```")) {
        body.push(lines[i] ?? "");
        i++;
      }
      i++;
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }
    const heading = /^\s{0,3}#{1,6}\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push({ kind: "h", lines: [heading[1] ?? ""] });
      i++;
      continue;
    }
    if (BULLET.test(line) || NUMBERED.test(line)) {
      const re = BULLET.test(line) ? BULLET : NUMBERED;
      const items: string[] = [];
      while (i < lines.length && re.test(lines[i] ?? "")) {
        items.push(re.exec(lines[i] ?? "")?.[1] ?? "");
        i++;
      }
      blocks.push({ kind: re === BULLET ? "ul" : "ol", items });
      continue;
    }
    const para: string[] = [];
    while (
      i < lines.length &&
      (lines[i] ?? "").trim() &&
      !BULLET.test(lines[i] ?? "") &&
      !NUMBERED.test(lines[i] ?? "") &&
      !/^\s{0,3}#{1,6}\s/.test(lines[i] ?? "") &&
      !(lines[i] ?? "").trimStart().startsWith("```")
    ) {
      para.push(lines[i] ?? "");
      i++;
    }
    if (para.length) blocks.push({ kind: "p", lines: para });
  }
  return blocks;
}
