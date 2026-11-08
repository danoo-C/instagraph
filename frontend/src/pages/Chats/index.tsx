import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useApiInfinite } from "@/api/useApi";
import { DataTable } from "@/components/DataTable";
import { dataTableColumnHelper, type SortingState } from "@/components/dataTableColumns";
import { ErrorCard } from "@/components/ErrorCard";
import { PersonBadge } from "@/components/PersonBadge";
import { Skeleton } from "@/components/Skeleton";
import { formatMs, trendArrow } from "@/features/chat/charts";
import type { ChatListItem, ChatListResponse, ChatSortKey } from "@/features/chat/types";
import { formatDate, formatNumber, formatPercent } from "@/lib/format";
import { Sparkline } from "@/pages/Chats/Sparkline";
import { PAGE_SIZE, parseListParams, sortingToParams } from "@/pages/Chats/listParams";

const KIND_LABEL: Record<ChatListItem["kind"], string> = {
  one_to_one: "1:1",
  group: "Group",
  request: "Request",
};

const col = dataTableColumnHelper<ChatListItem>();

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const h = setTimeout(() => setV(value), ms);
    return () => clearTimeout(h);
  }, [value, ms]);
  return v;
}

export default function ChatsPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const { q, sort, order } = parseListParams(params);
  const [input, setInput] = useState(q);
  const debounced = useDebounced(input.trim(), 300);

  // Debounced search → URL (?q=), keeping filters and other params.
  useEffect(() => {
    if (debounced === q) return;
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (debounced) next.set("q", debounced);
        else next.delete("q");
        return next;
      },
      { replace: true },
    );
  }, [debounced, q, setParams]);

  const query = useApiInfinite<ChatListResponse>(
    "/api/chats",
    { query: { q: q || undefined, sort, order, limit: PAGE_SIZE } },
    {
      cursorParam: "offset",
      getNextCursor: (p) => (p.offset + p.limit < p.total ? p.offset + p.limit : null),
    },
  );
  const pages = query.data?.pages;
  const rows = useMemo(() => pages?.flatMap((p) => p.items) ?? [], [pages]);
  const first = pages?.[0];
  const months = first?.sparkline_months;
  const total = first?.total;

  const sorting: SortingState = [{ id: sort, desc: order === "desc" }];
  const onSortingChange = useCallback(
    (updater: SortingState | ((old: SortingState) => SortingState)) => {
      const next = typeof updater === "function" ? updater(sorting) : updater;
      const p = sortingToParams(next);
      setParams((prev) => {
        const out = new URLSearchParams(prev);
        if (p) {
          out.set("sort", p.sort);
          out.set("order", p.order);
        } else {
          out.delete("sort");
          out.delete("order");
        }
        return out;
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- sorting derives from sort/order
    [sort, order, setParams],
  );

  const { hasNextPage, isFetchingNextPage, fetchNextPage } = query;
  const onEndReached = useCallback(() => {
    if (hasNextPage && !isFetchingNextPage) void fetchNextPage();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  const columns = useMemo(() => buildColumns(months ?? []), [months]);

  return (
    <div className="page">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="page-title">Chats</h1>
        {total != null && <span className="text-sm text-muted">{formatNumber(total)} chats</span>}
        <input
          type="search"
          className="input ml-auto w-full sm:w-72"
          placeholder="Search titles and people…"
          aria-label="Search chats"
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
      </div>
      {query.error ? (
        <ErrorCard error={query.error} onRetry={() => void query.refetch()} />
      ) : query.isLoading ? (
        <div className="flex flex-col gap-2" aria-busy="true">
          {Array.from({ length: 10 }, (_, i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      ) : (
        <>
          <DataTable
            data={rows}
            columns={columns}
            getRowId={(r) => String(r.thread_id)}
            sorting={sorting}
            onSortingChange={onSortingChange}
            manualSorting
            virtualize={{ height: 640, rowHeight: 40 }}
            onRowClick={(r) => navigate(`/chats/${r.thread_id}/summary${keepFilters(params)}`)}
            onEndReached={onEndReached}
            caption="Chats"
            empty={q ? `No chats match "${q}".` : "No chats in the current filters."}
          />
          <p className="text-xs text-muted" aria-live="polite">
            {isFetchingNextPage
              ? "Loading more…"
              : total != null
                ? `Showing ${formatNumber(rows.length)} of ${formatNumber(total)}`
                : null}
          </p>
        </>
      )}
    </div>
  );
}

/** Keep only global filter params when navigating to a chat. */
function keepFilters(params: URLSearchParams): string {
  const out = new URLSearchParams(params);
  out.delete("q");
  out.delete("sort");
  out.delete("order");
  const s = out.toString();
  return s ? `?${s}` : "";
}

function num(v: number | null | undefined) {
  return <span className="tabular-nums">{formatNumber(v)}</span>;
}

function buildColumns(months: string[]) {
  const sortable = (id: ChatSortKey) => ({ id, enableSorting: true });
  return col.columns([
    col.accessor("title", {
      ...sortable("title"),
      header: "Title",
      cell: (c) => (
        <span className="block max-w-[16rem] truncate font-medium" title={c.getValue()}>
          {c.getValue()}
        </span>
      ),
    }),
    col.accessor("kind", {
      ...sortable("kind"),
      header: "Type",
      cell: (c) => <span className="text-muted">{KIND_LABEL[c.getValue()]}</span>,
    }),
    col.accessor("n_participants", {
      ...sortable("participants"),
      header: "Participants",
      cell: (c) => {
        const others = c.row.original.participants.filter((p) => !p.is_owner);
        return (
          <span className="flex items-center gap-1" title={others.map((p) => p.name).join(", ")}>
            <span className="flex -space-x-1">
              {others.slice(0, 4).map((p) => (
                <PersonBadge
                  key={p.person_id}
                  name={p.name}
                  id={p.person_id}
                  color_index={p.color_index}
                  size="xs"
                />
              ))}
            </span>
            <span className="text-xs text-muted">{c.getValue()}</span>
          </span>
        );
      },
    }),
    col.accessor("messages", {
      ...sortable("messages"),
      header: "Messages",
      cell: (c) => num(c.getValue()),
    }),
    col.accessor("my_share", {
      ...sortable("my_share"),
      header: "My share",
      cell: (c) => formatPercent(c.getValue()),
    }),
    col.accessor("messages_last_30d", {
      ...sortable("messages_last_30d"),
      header: "Last 30 days",
      cell: (c) => num(c.getValue()),
    }),
    col.accessor("first_ts", {
      ...sortable("first_ts"),
      header: "First",
      cell: (c) => <span className="whitespace-nowrap">{formatDate(c.getValue())}</span>,
    }),
    col.accessor("last_ts", {
      ...sortable("last_ts"),
      header: "Last",
      cell: (c) => <span className="whitespace-nowrap">{formatDate(c.getValue())}</span>,
    }),
    col.accessor("duration_days", {
      ...sortable("duration_days"),
      header: "Duration",
      cell: (c) => <span className="whitespace-nowrap">{formatNumber(c.getValue())} d</span>,
    }),
    col.accessor("avg_per_day", {
      ...sortable("avg_per_day"),
      header: "Avg/day",
      cell: (c) => formatNumber(c.getValue(), { maximumFractionDigits: 2 }),
    }),
    col.accessor("median_reply_me_ms", {
      ...sortable("median_reply_me_ms"),
      header: "Reply (me)",
      cell: (c) => <span className="whitespace-nowrap">{formatMs(c.getValue())}</span>,
    }),
    col.accessor("median_reply_them_ms", {
      ...sortable("median_reply_them_ms"),
      header: "Reply (them)",
      cell: (c) => <span className="whitespace-nowrap">{formatMs(c.getValue())}</span>,
    }),
    col.accessor("emojis", {
      ...sortable("emojis"),
      header: "Emojis",
      cell: (c) => num(c.getValue()),
    }),
    col.accessor("calls", {
      ...sortable("calls"),
      header: "Calls",
      cell: (c) => num(c.getValue()),
    }),
    col.accessor("sparkline", {
      id: "sparkline",
      enableSorting: false,
      header: "Activity",
      cell: (c) => <Sparkline values={c.getValue()} months={months} />,
    }),
    col.accessor("trend_slope", {
      ...sortable("trend_slope"),
      header: "Trend",
      cell: (c) => {
        const a = trendArrow(c.row.original.trend);
        return (
          <span
            className={a.className}
            title={`${a.label} (slope ${formatNumber(c.getValue(), { maximumFractionDigits: 2 })})`}
          >
            <span aria-hidden="true">{a.symbol}</span>
            <span className="sr-only">{a.label}</span>
          </span>
        );
      },
    }),
  ]);
}
