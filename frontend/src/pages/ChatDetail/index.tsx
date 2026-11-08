import { Suspense, useCallback } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ErrorCard } from "@/components/ErrorCard";
import { PersonBadge } from "@/components/PersonBadge";
import { ChartSkeleton, Skeleton } from "@/components/Skeleton";
import { ChatProvider } from "@/features/chat/ChatProvider";
import { ChatPrintReport } from "@/features/print/ChatPrintReport";
import { PrintButton } from "@/features/print/PrintButton";
import { parseChatId, useChatHeader } from "@/features/chat/context";
import { toMessageQuery, messagesHref } from "@/lib/drilldown";
import type { ChatHeader } from "@/features/chat/types";
import { useT } from "@/i18n";
import { formatDateRange, formatNumber, formatPercent } from "@/lib/format";
import { FILTER_KEYS } from "@/state/filters";
import { DEFAULT_CHAT_TAB, visibleChatTabs } from "@/pages/ChatDetail/tabs";

const KIND_LABEL: Record<ChatHeader["kind"], string> = {
  one_to_one: "1:1",
  group: "Group",
  request: "Request",
};

/** `/compare?chats=<id>` keeping the global filters. */
function compareHref(id: string, current: URLSearchParams): string {
  const out = new URLSearchParams();
  for (const k of FILTER_KEYS) {
    const v = current.get(k);
    if (v != null) out.set(k, v);
  }
  out.set("chats", id);
  return `/compare?${out}`;
}

function ChatHeaderView({
  id,
  header,
  onPrint,
}: {
  id: string;
  header: ChatHeader;
  onPrint: () => void;
}) {
  const [params] = useSearchParams();
  return (
    <header className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="page-title min-w-0 truncate">{header.title}</h1>
        <span className="rounded-full border border-border px-2 py-0.5 text-xs text-muted">
          {KIND_LABEL[header.kind]}
        </span>
        <Link to={compareHref(id, params)} className="btn btn-sm ml-auto">
          Compare with…
        </Link>
        <PrintButton onPrint={onPrint} />
      </div>
      <ul className="flex flex-wrap gap-x-3 gap-y-1 text-sm" aria-label="Participants">
        {header.participants.slice(0, 24).map((p) => (
          <li key={p.person_id}>
            <Link to={`/people/${p.person_id}`} className="hover:underline">
              <PersonBadge
                name={p.is_owner ? `${p.name} (me)` : p.name}
                id={p.person_id}
                color_index={p.color_index}
                showName
              />
            </Link>
          </li>
        ))}
        {header.participants.length > 24 && (
          <li className="text-muted">+{header.participants.length - 24} more</li>
        )}
      </ul>
      <dl className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
        <div className="flex gap-1.5">
          <dt className="text-muted">Span</dt>
          <dd>
            {formatDateRange(header.span.start, header.span.end)}
            {header.span_days > 0 && (
              <span className="text-muted"> ({formatNumber(header.span_days)} days)</span>
            )}
          </dd>
        </div>
        <div className="flex gap-1.5">
          <dt className="text-muted">Messages</dt>
          <dd>
            <Link
              to={messagesHref(toMessageQuery(header.drilldown), params)}
              className="font-medium hover:underline"
            >
              {formatNumber(header.total_messages)}
            </Link>
          </dd>
        </div>
        <div className="flex gap-1.5">
          <dt className="text-muted">My share</dt>
          <dd>
            {formatPercent(header.my_share)}{" "}
            <span className="text-muted">({formatNumber(header.my_messages)})</span>
          </dd>
        </div>
      </dl>
    </header>
  );
}

export default function ChatDetailPage() {
  const t = useT();
  const { id = "", tab } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const printMode = params.get("print") === "1";
  const setPrintMode = useCallback(
    (on: boolean) =>
      setParams(
        (p) => {
          const next = new URLSearchParams(p);
          if (on) next.set("print", "1");
          else next.delete("print");
          return next;
        },
        { replace: !on },
      ),
    [setParams],
  );
  const startPrint = useCallback(() => setPrintMode(true), [setPrintMode]);
  const endPrint = useCallback(() => setPrintMode(false), [setPrintMode]);
  const navParams = new URLSearchParams(params);
  navParams.delete("print");
  const search = navParams.toString() ? `?${navParams}` : "";
  const header = useChatHeader(id);
  const isGroup = header.data?.is_group;
  const tabs = visibleChatTabs(isGroup);
  const active = tabs.find((d) => d.id === (tab ?? DEFAULT_CHAT_TAB));

  if (Number.isNaN(parseChatId(id)))
    return (
      <div className="page">
        <ErrorCard title="Chat not found" error={new Error(`Unknown chat "${id}".`)} />
      </div>
    );
  if (!active)
    return (
      <Navigate to={`/chats/${encodeURIComponent(id)}/${DEFAULT_CHAT_TAB}${search}`} replace />
    );
  const Active = active.component;

  return (
    <div className="page">
      <nav aria-label="Breadcrumb" className="text-xs text-muted">
        <Link to={`/chats${search}`} className="hover:underline">
          Chats
        </Link>{" "}
        /
      </nav>
      {header.data ? (
        <ChatHeaderView id={id} header={header.data} onPrint={startPrint} />
      ) : header.error ? (
        <ErrorCard error={header.error} onRetry={() => void header.refetch()} />
      ) : (
        <div className="flex flex-col gap-2" aria-busy="true">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-5 w-96 max-w-full" />
        </div>
      )}
      <ChatProvider chatId={id} header={header.data}>
        {printMode ? (
          <div className="mt-4">
            <ChatPrintReport chatId={id} onDone={endPrint} />
          </div>
        ) : (
          <Tabs.Root
            value={active.id}
            onValueChange={(v) => navigate(`/chats/${encodeURIComponent(id)}/${v}${search}`)}
            activationMode="manual"
            className="mt-4"
          >
            <Tabs.List
              aria-label="Chat sections"
              className="-mx-1 flex gap-1 overflow-x-auto border-b border-border px-1"
            >
              {tabs.map((d) => (
                <Tabs.Trigger
                  key={d.id}
                  value={d.id}
                  className="whitespace-nowrap border-b-2 border-transparent px-3 py-2 text-sm font-medium text-muted hover:text-fg data-[state=active]:border-accent data-[state=active]:text-fg"
                >
                  {t(d.label)}
                </Tabs.Trigger>
              ))}
            </Tabs.List>
            {/* Only the active tab is mounted (and its chunk loaded), so only its endpoint is requested. */}
            <Tabs.Content value={active.id} className="pt-4">
              <Suspense fallback={<ChartSkeleton height={240} />}>
                <Active chatId={id} />
              </Suspense>
            </Tabs.Content>
          </Tabs.Root>
        )}
      </ChatProvider>
    </div>
  );
}
