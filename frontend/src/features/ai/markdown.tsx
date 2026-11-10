import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { AlertIcon } from "@/components/icons";
import { parseBlocks, parseInline } from "@/features/ai/markdownParse";

/**
 * Minimal, safe Markdown for AI answers (docs/ai-assistant.md A4.3): paragraphs,
 * headings (rendered as bold lines), bullet and numbered lists, **bold**, *italic*,
 * `code`, and citations `[[msg:ID]]` / `[[chat:ID]]`. Everything becomes React elements;
 * no HTML from the model is ever interpreted. Links are shown as plain text.
 */

export interface CitationHandlers {
  onOpenMessage: (messageId: number) => void;
  /** Chats the conversation may read; a chat citation outside it shows a warning. */
  allowedChatIds: ReadonlySet<number>;
  chatTitle?: (chatId: number) => string | undefined;
}

function InlineNodes({ text, h }: { text: string; h: CitationHandlers }) {
  return (
    <>
      {parseInline(text).map((tok, i) => {
        switch (tok.kind) {
          case "text":
            return <span key={i}>{tok.text}</span>;
          case "bold":
            return <strong key={i}>{tok.text}</strong>;
          case "italic":
            return <em key={i}>{tok.text}</em>;
          case "code":
            return (
              <code key={i} className="rounded bg-surface-2 px-1 py-0.5 text-[0.85em]">
                {tok.text}
              </code>
            );
          case "msg":
            return (
              <button
                key={i}
                type="button"
                className="mx-0.5 inline-flex items-center rounded-full bg-accent/10 px-1.5 py-0 align-baseline text-xs font-medium text-accent hover:bg-accent/20"
                onClick={() => h.onOpenMessage(tok.id)}
                title="Open this message in its conversation"
              >
                message
              </button>
            );
          case "chat": {
            if (!h.allowedChatIds.has(tok.id)) {
              return (
                <span
                  key={i}
                  className="mx-0.5 inline-flex items-center gap-0.5 text-xs text-muted"
                  title="This chat isn't in the conversation's allowed chats"
                >
                  <AlertIcon width={12} height={12} aria-hidden="true" />
                  chat {tok.id}
                </span>
              );
            }
            const title = h.chatTitle?.(tok.id) ?? "chat";
            return (
              <Link
                key={i}
                to={`/chats/${tok.id}/summary`}
                className="mx-0.5 inline-flex items-center rounded-full bg-accent/10 px-1.5 py-0 align-baseline text-xs font-medium text-accent hover:bg-accent/20"
              >
                {title}
              </Link>
            );
          }
        }
      })}
    </>
  );
}

export function Markdown({ text, handlers }: { text: string; handlers: CitationHandlers }) {
  const nodes: ReactNode[] = parseBlocks(text).map((b, i) => {
    switch (b.kind) {
      case "h":
        return (
          <p key={i} className="font-semibold">
            <InlineNodes text={b.lines.join(" ")} h={handlers} />
          </p>
        );
      case "p":
        return (
          <p key={i}>
            {b.lines.map((line, j) => (
              <span key={j}>
                {j > 0 && <br />}
                <InlineNodes text={line} h={handlers} />
              </span>
            ))}
          </p>
        );
      case "ul":
      case "ol": {
        const Tag = b.kind;
        return (
          <Tag key={i} className={b.kind === "ul" ? "list-disc pl-5" : "list-decimal pl-5"}>
            {b.items.map((item, j) => (
              <li key={j}>
                <InlineNodes text={item} h={handlers} />
              </li>
            ))}
          </Tag>
        );
      }
      case "code":
        return (
          <pre key={i} className="overflow-x-auto rounded-md bg-surface-2 p-2 text-xs">
            {b.text}
          </pre>
        );
    }
  });
  return <div className="flex flex-col gap-2 text-sm leading-relaxed">{nodes}</div>;
}
