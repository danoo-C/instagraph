import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useApi } from "@/api/useApi";
import { DataTable } from "@/components/DataTable";
import { dataTableColumnHelper, type SortingState } from "@/components/dataTableColumns";
import { ErrorCard } from "@/components/ErrorCard";
import { PersonBadge } from "@/components/PersonBadge";
import { Skeleton } from "@/components/Skeleton";
import type { Schemas } from "@/features/people/apiTypes";
import { useDebounced } from "@/features/people/useDebounced";
import { formatDate, formatNumber } from "@/lib/format";

type Row = Schemas["PersonListItem"];
type Sort = "exchanged" | "messages" | "name" | "last_contact" | "threads";

const PAGE_SIZE = 50;
const SORTS: Array<{ value: Sort; label: string }> = [
  { value: "exchanged", label: "Messages exchanged" },
  { value: "messages", label: "Messages they sent" },
  { value: "threads", label: "Chats in common" },
  { value: "last_contact", label: "Last contact" },
  { value: "name", label: "Name" },
];
const COLUMN_SORT: Record<string, Sort> = {
  name: "name",
  exchanged: "exchanged",
  sent: "messages",
  threads: "threads",
  last: "last_contact",
};

const col = dataTableColumnHelper<Row>();

export default function PeoplePage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const sort = (params.get("sort") as Sort | null) ?? "exchanged";
  const order = params.get("order") === "asc" ? "asc" : "desc";
  const page = Math.max(0, Number(params.get("page") ?? 0) || 0);
  const [search, setSearch] = useState(params.get("q") ?? "");
  const q = useDebounced(search.trim());

  const update = (patch: Record<string, string | null>) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(patch)) {
          if (v == null || v === "") next.delete(k);
          else next.set(k, v);
        }
        return next;
      },
      { replace: true },
    );

  useEffect(() => {
    if ((params.get("q") ?? "") !== q) update({ q: q || null, page: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to the debounced value
  }, [q]);

  const query = useApi("/api/people", {
    query: { q: q || undefined, sort, order, offset: page * PAGE_SIZE, limit: PAGE_SIZE },
  });
  const data = query.data;
  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const columns = useMemo(
    () =>
      col.columns([
        col.accessor((r) => r.person.name, {
          id: "name",
          header: "Person",
          cell: (c) => {
            const r = c.row.original;
            return (
              <Link
                to={`/people/${r.person.person_id}`}
                className="inline-flex items-center gap-2 hover:text-accent"
                onClick={(e) => e.stopPropagation()}
              >
                <PersonBadge
                  name={r.person.name}
                  id={r.person.person_id}
                  color_index={r.person.color_index}
                  size="sm"
                />
                <span className="font-medium">{r.person.name}</span>
                {r.person.is_owner && (
                  <span className="rounded bg-accent/10 px-1.5 text-xs text-accent">Me</span>
                )}
                {r.is_deleted_account && (
                  <span className="text-xs text-muted">(deleted account)</span>
                )}
              </Link>
            );
          },
        }),
        col.accessor("messages_exchanged", {
          id: "exchanged",
          header: "Exchanged",
          cell: (c) => formatNumber(c.getValue()),
        }),
        col.accessor("messages_sent", {
          id: "sent",
          header: "They sent",
          cell: (c) => formatNumber(c.getValue()),
        }),
        col.accessor("threads_in_common", {
          id: "threads",
          header: "Chats in common",
          cell: (c) => {
            const r = c.row.original;
            return (
              <span title={`${r.one_to_one_threads} 1:1 · ${r.group_threads} group`}>
                {formatNumber(r.threads_in_common)}
                <span className="ml-1 text-xs text-muted">
                  ({r.one_to_one_threads} 1:1 · {r.group_threads} groups)
                </span>
              </span>
            );
          },
        }),
        col.accessor((r) => r.last_contact ?? "", {
          id: "last",
          header: "Last contact",
          cell: (c) => formatDate(c.row.original.last_contact),
        }),
      ]),
    [],
  );

  const columnId = Object.entries(COLUMN_SORT).find(([, s]) => s === sort)?.[0] ?? "exchanged";
  const sorting: SortingState = [{ id: columnId, desc: order === "desc" }];

  return (
    <div className="page">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">People</h1>
          <p className="text-sm text-muted">
            Everyone you talked with across all chats
            {data ? ` · ${formatNumber(total)} people` : ""}.
          </p>
        </div>
        <Link to="/compare?people=" className="btn btn-sm" data-print-hide>
          Compare people
        </Link>
      </div>

      <div className="flex flex-wrap items-center gap-2" data-print-hide>
        <label className="sr-only" htmlFor="people-search">
          Search people
        </label>
        <input
          id="people-search"
          type="search"
          className="input h-9 w-64"
          placeholder="Search by name…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <label className="sr-only" htmlFor="people-sort">
          Sort by
        </label>
        <select
          id="people-sort"
          className="input h-9"
          value={sort}
          onChange={(e) => update({ sort: e.target.value, page: null })}
        >
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn btn-sm h-9"
          onClick={() => update({ order: order === "desc" ? "asc" : null, page: null })}
          aria-label={`Sort order: ${order === "desc" ? "descending" : "ascending"}`}
        >
          {order === "desc" ? "↓ Desc" : "↑ Asc"}
        </button>
      </div>

      {query.error ? (
        <ErrorCard error={query.error} onRetry={() => void query.refetch()} />
      ) : query.isPending ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
      ) : (
        <DataTable
          data={data?.items ?? []}
          columns={columns}
          getRowId={(r) => String(r.person.person_id)}
          manualSorting
          sorting={sorting}
          onSortingChange={(updater) => {
            const next = typeof updater === "function" ? updater(sorting) : updater;
            const first = next[0];
            if (!first) {
              update({ order: order === "desc" ? "asc" : null });
              return;
            }
            const s = COLUMN_SORT[first.id] ?? "exchanged";
            update({
              sort: s === "exchanged" ? null : s,
              order: first.desc ? null : "asc",
              page: null,
            });
          }}
          onRowClick={(r) => navigate(`/people/${r.person.person_id}`)}
          caption="People"
          empty={q ? `Nobody matches “${q}”.` : "No people in the current filters."}
        />
      )}

      {pages > 1 && (
        <nav
          className="flex items-center justify-center gap-2 text-sm"
          aria-label="Pagination"
          data-print-hide
        >
          <button
            type="button"
            className="btn btn-sm"
            disabled={page === 0}
            onClick={() => update({ page: page - 1 > 0 ? String(page - 1) : null })}
          >
            ← Previous
          </button>
          <span className="text-muted">
            Page {page + 1} of {pages}
          </span>
          <button
            type="button"
            className="btn btn-sm"
            disabled={page + 1 >= pages}
            onClick={() => update({ page: String(page + 1) })}
          >
            Next →
          </button>
        </nav>
      )}
    </div>
  );
}
