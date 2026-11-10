import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ErrorCard } from "@/components/ErrorCard";
import { Skeleton } from "@/components/Skeleton";
import { ChatView } from "@/features/ai/ChatView";
import { NewConversationDialog } from "@/features/ai/NewConversationDialog";
import { SetupCard } from "@/features/ai/SetupCard";
import {
  deleteConversation,
  refreshAi,
  useAiConversation,
  useAiConversations,
  useAiStatus,
  type AiStatus,
} from "@/features/ai/api";
import { cn } from "@/lib/utils";

/** AI assistant (docs/ai-assistant.md A4): setup card while off, else conversations. */
export default function AiPage() {
  const status = useAiStatus();
  return (
    // Full height like the Chats page: only the message list scrolls.
    <div className="page min-h-0 max-w-none flex-1">
      <h1 className="page-title">AI</h1>
      {status.error ? (
        <ErrorCard error={status.error} onRetry={() => void status.refetch()} />
      ) : !status.data ? (
        <Skeleton className="h-64 w-full" />
      ) : !status.data.enabled ? (
        <SetupCard status={status.data} />
      ) : (
        <Conversations status={status.data} />
      )}
    </div>
  );
}

function Conversations({ status }: { status: AiStatus }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const list = useAiConversations(true);
  const conv = useAiConversation(id, true);
  const [newOpen, setNewOpen] = useState(false);
  const items = list.data?.items ?? [];

  const remove = async (convId: string) => {
    if (!window.confirm("Delete this conversation?")) return;
    await deleteConversation(convId);
    if (convId === id) navigate("/ai");
    await refreshAi();
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 md:flex-row">
      <aside className="flex flex-col gap-2 md:w-64 md:shrink-0" aria-label="AI conversations">
        <button type="button" className="btn btn-primary" onClick={() => setNewOpen(true)}>
          + New conversation
        </button>
        <ul className="flex max-h-48 flex-col gap-0.5 overflow-y-auto md:max-h-none">
          {items.map((c) => (
            <li key={c.id} className="group flex items-center gap-1">
              <Link
                to={`/ai/${c.id}`}
                className={cn(
                  "min-w-0 flex-1 truncate rounded-md px-2 py-1.5 text-sm",
                  c.id === id
                    ? "bg-accent/10 text-accent"
                    : "text-muted hover:bg-surface-2 hover:text-fg",
                )}
                title={c.title}
              >
                {c.title}
              </Link>
              <button
                type="button"
                className="icon-btn opacity-0 focus:opacity-100 group-hover:opacity-100"
                aria-label={`Delete ${c.title}`}
                onClick={() => void remove(c.id)}
              >
                ×
              </button>
            </li>
          ))}
          {list.data && items.length === 0 && (
            <li className="px-2 py-1.5 text-xs text-muted">No conversations yet.</li>
          )}
        </ul>
      </aside>

      <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Conversation">
        {!id ? (
          <div className="card m-auto max-w-md p-6 text-center text-sm text-muted">
            <p className="font-medium text-fg">Start a conversation</p>
            <p className="mt-1">
              Pick which chats the AI may read, then ask about them. Each conversation keeps its own
              chats and name setting.
            </p>
            <button type="button" className="btn btn-primary mt-3" onClick={() => setNewOpen(true)}>
              New conversation
            </button>
          </div>
        ) : conv.error ? (
          <ErrorCard error={conv.error} onRetry={() => void conv.refetch()} />
        ) : !conv.data ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <ChatView key={conv.data.id} conv={conv.data} status={status} />
        )}
      </section>

      <NewConversationDialog
        open={newOpen}
        onOpenChange={setNewOpen}
        status={status}
        onCreated={(c) => {
          setNewOpen(false);
          void refreshAi();
          navigate(`/ai/${c.id}`);
        }}
      />
    </div>
  );
}
