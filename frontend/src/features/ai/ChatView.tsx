import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { CloseIcon } from "@/components/icons";
import { ContextDialog } from "@/features/messages/ContextDialog";
import { Markdown, type CitationHandlers } from "@/features/ai/markdown";
import { ScopePicker } from "@/features/ai/ScopePicker";
import {
  fetchLastRequest,
  formatTokens,
  patchConversation,
  refreshAi,
  stopAnswer,
  streamMessage,
  type AiConversation,
  type AiLastRequest,
  type AiStatus,
  type AiToolActivity,
} from "@/features/ai/api";
import { applyEvent, EMPTY_USAGE, type LiveTurn } from "@/features/ai/turn";
import { cn } from "@/lib/utils";

export function ChatView({
  conv,
  status,
  initialMessage,
  onInitialSent,
}: {
  conv: AiConversation;
  status: AiStatus;
  /** Sent once right away when the conversation is empty (a new report). */
  initialMessage?: string;
  onInitialSent?: () => void;
}) {
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [input, setInput] = useState("");
  const [sendError, setSendError] = useState<string | null>(null);
  const [openMessage, setOpenMessage] = useState<number | null>(null);
  const [scopeOpen, setScopeOpen] = useState(false);
  const [requestOpen, setRequestOpen] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const provider = status.providers.find((p) => p.id === conv.provider);

  const handlers: CitationHandlers = useMemo(() => {
    const titles = new Map(conv.scope_chats.map((c) => [c.chat_id, c.title]));
    return {
      onOpenMessage: setOpenMessage,
      allowedChatIds: new Set(titles.keys()),
      chatTitle: (id) => titles.get(id),
    };
  }, [conv.scope_chats]);

  // The backend saves a turn as "running" when it starts; while we stream it ourselves,
  // show our live copy instead of the saved one.
  const saved =
    live && conv.turns.at(-1)?.status === "running" ? conv.turns.slice(0, -1) : conv.turns;
  const turns: LiveTurn[] = live ? [...saved, live] : conv.turns;
  const busy = live != null || conv.running;

  // An answer running elsewhere (e.g. after a reload): poll until it finishes.
  useEffect(() => {
    if (!conv.running || live) return;
    const timer = setInterval(() => void refreshAi(), 1500);
    return () => clearInterval(timer);
  }, [conv.running, live]);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns.length, live?.answer.length, live?.tools.length]);

  const send = async (override?: string) => {
    const text = (override ?? input).trim();
    if (!text || busy) return;
    setSendError(null);
    if (override === undefined) setInput("");
    let turn: LiveTurn = {
      user_text: text,
      answer: "",
      tools: [],
      usage: EMPTY_USAGE,
      status: "running",
      error: null,
    };
    setLive(turn);
    try {
      await streamMessage(conv.id, text, (ev) => {
        turn = applyEvent(turn, ev);
        setLive(turn);
      });
    } catch (e) {
      setSendError(e instanceof Error ? e.message : "Sending failed.");
      setInput(text);
    } finally {
      await refreshAi();
      setLive(null);
    }
  };

  const initialSent = useRef(false);
  useEffect(() => {
    if (!initialMessage || initialSent.current || conv.turns.length > 0) return;
    initialSent.current = true;
    onInitialSent?.();
    void send(initialMessage);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, for a new report
  }, [initialMessage]);

  const stop = () => void stopAnswer(conv.id);

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send();
    } else if (e.key === "Escape" && busy) {
      e.preventDefault();
      stop();
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <header className="flex flex-wrap items-center gap-2">
        <h2 className="min-w-0 flex-1 truncate text-base font-semibold" title={conv.title}>
          {conv.title}
        </h2>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => setScopeOpen(true)}
          title="Chats the AI may read in this conversation"
        >
          {conv.scope_chats.length} chat{conv.scope_chats.length === 1 ? "" : "s"} ✎
        </button>
        <span
          className={cn(
            "rounded-full px-2 py-1 text-xs",
            conv.hide_names
              ? "bg-surface-2 text-muted"
              : "bg-amber-500/15 text-amber-700 dark:text-amber-300",
          )}
          title={
            (conv.hide_names
              ? "Names, usernames and chat titles are replaced with placeholders before anything is sent. Message content can still identify people."
              : "Real names are sent to the AI in this conversation.") +
            (conv.names_locked ? " Fixed for this conversation; start a new one to change it." : "")
          }
        >
          {conv.hide_names ? "Names: hidden 🔒" : "Names: visible"}
        </span>
        <span className="text-xs text-muted">{provider?.label ?? conv.provider}</span>
        <button
          type="button"
          className="btn btn-sm"
          disabled={conv.turns.length === 0}
          onClick={() => setRequestOpen(true)}
        >
          What was sent
        </button>
      </header>
      {conv.scope_missing > 0 && (
        <p className="text-xs text-muted">
          {conv.scope_missing} allowed chat(s) no longer exist in the current data.
        </p>
      )}

      <div
        ref={scroller}
        className="card flex min-h-[16rem] flex-1 flex-col gap-5 overflow-y-auto p-4"
      >
        {turns.length === 0 ? (
          <EmptyHint scopeSize={conv.scope_chats.length} />
        ) : (
          turns.map((t, i) => <TurnView key={i} turn={t} handlers={handlers} live={t === live} />)
        )}
        {conv.running && !live && (
          <p className="text-sm text-muted">An answer is still being written…</p>
        )}
      </div>

      <div className="flex flex-col gap-1">
        {sendError && <p className="text-sm text-danger">{sendError}</p>}
        {conv.scope_chats.length === 0 && (
          <p className="text-xs text-muted">
            No chats are allowed yet, so the AI can&apos;t read any data. Use the “chats” button.
          </p>
        )}
        <div className="flex items-end gap-2">
          <textarea
            className="input min-h-[2.75rem] flex-1 resize-y"
            rows={2}
            placeholder="Ask about your chats…  (Enter to send, Shift+Enter for a new line)"
            aria-label="Message"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
          />
          {busy ? (
            <button type="button" className="btn" onClick={stop}>
              Stop
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-primary"
              disabled={!input.trim()}
              onClick={() => void send()}
            >
              Send
            </button>
          )}
        </div>
      </div>

      <ContextDialog messageId={openMessage} onClose={() => setOpenMessage(null)} />
      <ScopeDialog conv={conv} open={scopeOpen} onOpenChange={setScopeOpen} />
      <RequestDialog convId={conv.id} open={requestOpen} onOpenChange={setRequestOpen} />
    </div>
  );
}

function EmptyHint({ scopeSize }: { scopeSize: number }) {
  return (
    <div className="m-auto max-w-md text-center text-sm text-muted">
      <p className="font-medium text-fg">Ask anything about your chats</p>
      <p className="mt-1">
        For example: “Who replies faster, me or them?”, “When was our chat most active, and why?”,
        or “What do we talk about most?”
      </p>
      <p className="mt-2 text-xs">
        {scopeSize} chat{scopeSize === 1 ? "" : "s"} allowed in this conversation.
      </p>
    </div>
  );
}

function TurnView({
  turn,
  handlers,
  live,
}: {
  turn: LiveTurn;
  handlers: CitationHandlers;
  live: boolean;
}) {
  const tokens = turn.usage.input_tokens + turn.usage.output_tokens;
  return (
    <div className="flex flex-col gap-2">
      <div className="ml-auto max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-accent/10 px-3 py-2 text-sm">
        {turn.user_text}
      </div>
      <div className="flex max-w-[95%] flex-col gap-1.5" aria-live={live ? "polite" : undefined}>
        {turn.tools.length > 0 && <ToolActivity tools={turn.tools} tokens={tokens} />}
        {turn.answer ? (
          <Markdown text={turn.answer} handlers={handlers} />
        ) : (
          live && turn.status === "running" && <p className="text-sm text-muted">Thinking…</p>
        )}
        {live && turn.status === "running" && turn.answer && (
          <span className="inline-block h-4 w-1.5 animate-pulse bg-muted" aria-hidden="true" />
        )}
        {turn.status === "stopped" && <p className="text-xs text-muted">Stopped.</p>}
        {turn.status === "error" && (
          <p className="text-sm text-danger">{turn.error ?? "Something went wrong."}</p>
        )}
        {!live && turn.tools.length === 0 && tokens > 0 && (
          <p className="text-xs text-muted">{formatTokens(tokens)} tokens</p>
        )}
      </div>
    </div>
  );
}

function ToolActivity({ tools, tokens }: { tools: AiToolActivity[]; tokens: number }) {
  return (
    <details className="text-xs text-muted">
      <summary className="cursor-pointer select-none">
        {tools.length} tool call{tools.length === 1 ? "" : "s"}
        {tokens > 0 && ` · ${formatTokens(tokens)} tokens`}
      </summary>
      <ul className="mt-1 flex flex-col gap-1 border-l border-border pl-3">
        {tools.map((t) => (
          <li key={t.id}>
            <span className="font-mono text-fg">{t.name}</span>{" "}
            <span className="font-mono">{JSON.stringify(t.args)}</span>
            {t.summary && (
              <span className={t.is_error ? "text-danger" : ""}>
                {" "}
                → {t.summary}
                {t.truncated && " (cut to fit)"}
              </span>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

function ScopeDialog({
  conv,
  open,
  onOpenChange,
}: {
  conv: AiConversation;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/50" />
        <Dialog.Content className="card fixed inset-x-2 top-8 z-50 mx-auto flex max-w-xl flex-col gap-3 p-4">
          <div className="flex items-start justify-between gap-2">
            <div>
              <Dialog.Title className="text-base font-semibold">Allowed chats</Dialog.Title>
              <Dialog.Description className="text-sm text-muted">
                {conv.turns.length > 0
                  ? "The AI is told about the change with your next message."
                  : "The AI can read only these chats."}
              </Dialog.Description>
            </div>
            <Dialog.Close className="icon-btn" aria-label="Close">
              <CloseIcon />
            </Dialog.Close>
          </div>
          {open && <ScopeBody conv={conv} onDone={() => onOpenChange(false)} />}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function ScopeBody({ conv, onDone }: { conv: AiConversation; onDone: () => void }) {
  const [ids, setIds] = useState<number[]>(() => conv.scope_chats.map((c) => c.chat_id));
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await patchConversation(conv.id, { chat_ids: ids });
      await refreshAi();
      onDone();
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <ScopePicker selected={ids} onChange={setIds} />
      <div className="flex justify-end gap-2">
        <Dialog.Close className="btn">Cancel</Dialog.Close>
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy}
          onClick={() => void save()}
        >
          Save
        </button>
      </div>
    </>
  );
}

function RequestDialog({
  convId,
  open,
  onOpenChange,
}: {
  convId: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/50" />
        <Dialog.Content className="card fixed inset-x-2 inset-y-4 z-50 mx-auto flex max-w-3xl flex-col gap-3 p-4 sm:inset-y-8">
          <div className="flex items-start justify-between gap-2">
            <div>
              <Dialog.Title className="text-base font-semibold">What was sent</Dialog.Title>
              <Dialog.Description className="text-sm text-muted">
                The last answer&apos;s request, exactly as the AI saw it.
              </Dialog.Description>
            </div>
            <Dialog.Close className="icon-btn" aria-label="Close">
              <CloseIcon />
            </Dialog.Close>
          </div>
          {open && <RequestBody convId={convId} />}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function RequestBody({ convId }: { convId: string }) {
  const [data, setData] = useState<AiLastRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    fetchLastRequest(convId).then(
      (d) => alive && setData(d),
      (e: unknown) => alive && setError(e instanceof Error ? e.message : "Couldn't load it."),
    );
    return () => {
      alive = false;
    };
  }, [convId]);
  return (
    <div className="min-h-0 flex-1 overflow-y-auto text-xs">
      {error && <p className="text-danger">{error}</p>}
      {!data && !error && <p className="text-muted">Loading…</p>}
      {data && (
        <div className="flex flex-col gap-3">
          <p className="text-muted">
            {data.provider} · {data.model} · {data.tools.length} tools available ·{" "}
            {data.tool_calls.length} called
          </p>
          <Block title="Your message">{data.user_message}</Block>
          {data.tool_calls.map((c, i) => (
            <Block key={i} title={`Tool: ${c.name} ${JSON.stringify(c.args)}`}>
              {typeof c.result === "string" ? c.result : JSON.stringify(c.result, null, 1)}
            </Block>
          ))}
          <Block title="System prompt">{data.system_prompt}</Block>
        </div>
      )}
    </div>
  );
}

function Block({ title, children }: { title: string; children: string }) {
  return (
    <section>
      <h3 className="mb-1 font-medium">{title}</h3>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-surface-2 p-2">
        {children}
      </pre>
    </section>
  );
}
