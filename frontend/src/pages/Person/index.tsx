import { categoryTable, timeSeriesTable } from "@/charts/tables";
import { useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ChartCard } from "@/charts/ChartCard";
import { EChart } from "@/charts/EChart";
import { useApi } from "@/api/useApi";
import { DataTable } from "@/components/DataTable";
import { dataTableColumnHelper } from "@/components/dataTableColumns";
import { EmptyState } from "@/components/EmptyState";
import { ErrorCard } from "@/components/ErrorCard";
import { PersonBadge } from "@/components/PersonBadge";
import { Skeleton } from "@/components/Skeleton";
import { Tile, TileGrid } from "@/components/Tile";
import type { Schemas } from "@/features/people/apiTypes";
import { categoryOption, isEmptyChart, timeSeriesOption } from "@/features/people/charts";
import { bestIndex, formatScalar } from "@/features/people/formatScalar";
import { drilldownQuery, useOpenMessages } from "@/lib/drilldown";
import { PrintButton } from "@/features/print/PrintButton";
import { formatDate, formatNumber, formatPercent, type ValueFormat } from "@/lib/format";
import { cn } from "@/lib/utils";

type Detail = Schemas["PersonDetailResponse"];
type Thread = Schemas["PersonThread"];

function tileFormat(unit: Schemas["Tile"]["unit"]): { format: ValueFormat; scale: number } {
  switch (unit) {
    case "ratio":
      return { format: "percent", scale: 1 };
    case "ms":
      return { format: "duration", scale: 1 / 1000 };
    case "s":
      return { format: "duration", scale: 1 };
    case "text":
    case "emoji":
    case "date":
    case "datetime":
      return { format: "text", scale: 1 };
    default:
      return { format: "number", scale: 1 };
  }
}

function DetailTiles({ tiles }: { tiles: Detail["tiles"] }) {
  return (
    <TileGrid>
      {tiles.map((tile) => {
        const { format, scale } = tileFormat(tile.unit);
        const value =
          typeof tile.value === "number"
            ? tile.value * scale
            : tile.unit === "date" || tile.unit === "datetime"
              ? formatScalar(tile.value, tile.unit)
              : typeof tile.value === "boolean"
                ? String(tile.value)
                : tile.value;
        return (
          <Tile
            key={tile.key}
            label={tile.label}
            value={value}
            format={format}
            hint={tile.detail ?? undefined}
            query={drilldownQuery(tile.drilldown)}
          />
        );
      })}
    </TileGrid>
  );
}

const threadCol = dataTableColumnHelper<Thread>();

function ThreadsTable({ threads, isMe }: { threads: Thread[]; isMe: boolean }) {
  const navigate = useNavigate();
  const columns = useMemo(
    () =>
      threadCol.columns([
        threadCol.accessor("title", {
          header: "Chat",
          cell: (c) => (
            <Link
              to={`/chats/${c.row.original.thread_id}`}
              className="font-medium hover:text-accent"
              onClick={(e) => e.stopPropagation()}
            >
              {c.getValue()}
              {c.row.original.is_group && <span className="ml-1 text-xs text-muted">group</span>}
              {c.row.original.category !== "inbox" && (
                <span className="ml-1 text-xs text-muted">{c.row.original.category}</span>
              )}
            </Link>
          ),
        }),
        threadCol.accessor("messages", {
          header: "Messages",
          cell: (c) => formatNumber(c.getValue()),
        }),
        ...(isMe
          ? []
          : [
              threadCol.accessor("their_messages", {
                header: "Theirs",
                cell: (c) => formatNumber(c.getValue()),
              }),
            ]),
        threadCol.accessor("my_messages", {
          header: "Mine",
          cell: (c) => formatNumber(c.getValue()),
        }),
        threadCol.accessor((r) => r.last_ts ?? "", {
          id: "last",
          header: "Last message",
          cell: (c) => formatDate(c.row.original.last_ts),
        }),
      ]),
    [isMe],
  );
  return (
    <DataTable
      data={threads}
      columns={columns}
      getRowId={(r) => String(r.thread_id)}
      initialSorting={[{ id: "messages", desc: true }]}
      onRowClick={(r) => navigate(`/chats/${r.thread_id}`)}
      virtualize={threads.length > 30 ? { height: 420 } : undefined}
      caption={isMe ? "My chats" : "Chats in common"}
      empty="No chats in the current filters."
    />
  );
}

function StatsTable({ detail }: { detail: Detail }) {
  const isMe = detail.is_me;
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <caption className="sr-only">{isMe ? "My stats" : "Their stats vs mine"}</caption>
        <thead className="bg-surface-2 text-left">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">
              Metric
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              {isMe ? "Me" : detail.person.name}
            </th>
            {!isMe && (
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Me
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {detail.stats.map((row) => {
            const best = isMe
              ? null
              : bestIndex([row.person_value, row.my_value ?? null], row.lower_is_better);
            return (
              <tr key={row.key} className="border-t border-border">
                <th scope="row" className="px-3 py-1.5 text-left font-normal">
                  {row.label}
                </th>
                <td
                  className={cn(
                    "px-3 py-1.5 text-right tabular-nums",
                    best === 0 && "font-semibold text-accent",
                  )}
                >
                  {formatScalar(row.person_value, row.unit)}
                </td>
                {!isMe && (
                  <td
                    className={cn(
                      "px-3 py-1.5 text-right tabular-nums",
                      best === 1 && "font-semibold text-accent",
                    )}
                  >
                    {formatScalar(row.my_value ?? null, row.unit)}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TopEmojis({ detail }: { detail: Detail }) {
  const open = useOpenMessages();
  if (detail.top_emojis.length === 0) return <p className="text-sm text-muted">No emojis.</p>;
  return (
    <ul className="flex flex-wrap gap-2">
      {detail.top_emojis.map((e) => (
        <li key={e.emoji}>
          <button
            type="button"
            className="btn gap-2"
            onClick={() => open({ sender: detail.person.person_id, emoji: e.emoji })}
            title={`Open messages from ${detail.person.name} with ${e.emoji}`}
          >
            <span className="text-xl leading-none">{e.emoji}</span>
            <span className="tabular-nums text-muted">{formatNumber(e.count)}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function TopContacts({ contacts }: { contacts: NonNullable<Detail["top_contacts"]> }) {
  const max = Math.max(1, ...contacts.map((c) => c.messages_exchanged));
  return (
    <ol className="flex flex-col gap-1.5">
      {contacts.map((c, i) => (
        <li key={c.person.person_id} className="flex items-center gap-2 text-sm">
          <span className="w-5 text-right text-xs text-muted">{i + 1}</span>
          <Link
            to={`/people/${c.person.person_id}`}
            className="flex min-w-0 flex-1 items-center gap-2 hover:text-accent"
          >
            <PersonBadge
              name={c.person.name}
              id={c.person.person_id}
              color_index={c.person.color_index}
              size="sm"
            />
            <span className="w-40 truncate">{c.person.name}</span>
            <span
              className="relative h-2 flex-1 overflow-hidden rounded bg-surface-2"
              aria-hidden="true"
            >
              <span
                className="absolute inset-y-0 left-0 rounded bg-accent/70"
                style={{ width: `${(c.messages_exchanged / max) * 100}%` }}
              />
            </span>
          </Link>
          <span className="w-16 text-right tabular-nums">{formatNumber(c.messages_exchanged)}</span>
        </li>
      ))}
    </ol>
  );
}

export default function PersonPage() {
  const { id } = useParams();
  const personId = Number(id);
  const valid = Number.isFinite(personId);
  const q = useApi(
    "/api/people/{person_id}",
    { path: { person_id: personId } },
    { enabled: valid, keepPrevious: false },
  );
  const open = useOpenMessages();

  if (!valid)
    return (
      <div className="page">
        <EmptyState title="Unknown person" />
      </div>
    );
  if (q.error) {
    return (
      <div className="page">
        <Link to="/people" className="text-sm text-muted hover:text-accent">
          ← People
        </Link>
        <ErrorCard error={q.error} onRetry={() => void q.refetch()} />
      </div>
    );
  }
  const d = q.data;
  const pct = (v: number) => formatPercent(v);

  return (
    <div className="page">
      <Link to="/people" className="text-sm text-muted hover:text-accent" data-print-hide>
        ← People
      </Link>
      <header className="flex flex-wrap items-center gap-3">
        {d ? (
          <>
            <PersonBadge
              name={d.person.name}
              id={d.person.person_id}
              color_index={d.person.color_index}
              size="lg"
            />
            <div className="min-w-0 flex-1">
              <h1 className="page-title">
                {d.is_me ? `Me · ${d.person.name}` : d.person.name}
                {d.is_deleted_account && (
                  <span className="ml-2 text-sm text-muted">(deleted account)</span>
                )}
              </h1>
              <p className="text-sm text-muted">
                {d.is_me
                  ? "My habits across all chats."
                  : `${formatNumber(d.threads.length)} chats in common.`}
              </p>
            </div>
            <div className="flex gap-2" data-print-hide>
              {!d.is_me && (
                <Link className="btn btn-sm" to={`/compare?people=${d.person.person_id}`}>
                  Compare
                </Link>
              )}
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => open({ sender: d.person.person_id })}
              >
                {d.is_me ? "My messages" : "Their messages"}
              </button>
              <PrintButton />
            </div>
          </>
        ) : (
          <Skeleton className="h-12 w-72" />
        )}
      </header>

      {d ? (
        <DetailTiles tiles={d.tiles} />
      ) : (
        <TileGrid>
          {Array.from({ length: 6 }, (_, i) => (
            <Tile key={i} label="…" value={null} loading />
          ))}
        </TileGrid>
      )}

      <ChartCard
        title={d?.is_me ? "My activity per month" : "Monthly activity: them vs me"}
        info="Messages per month in your shared chats."
        query={q}
        empty={q.isSuccess && isEmptyChart(d?.activity)}
        data={d ? timeSeriesTable(d.activity) : null}
      >
        {d && (
          <EChart
            option={timeSeriesOption(d.activity, { format: (v) => formatNumber(v), area: true })}
          />
        )}
      </ChartCard>

      <div className="card-grid">
        <ChartCard
          title="Hour of day"
          info="Share of each person's messages sent in each hour (local time)."
          query={q}
          empty={q.isSuccess && isEmptyChart(d?.hours)}
          data={d ? categoryTable(d.hours, "Hour") : null}
          height={280}
        >
          {d && (
            <EChart
              height={280}
              option={categoryOption(d.hours, { format: pct, hours: true, line: true })}
              onEvents={{
                click: (p: { dataIndex: number }) =>
                  open({ sender: d.person.person_id, hour: p.dataIndex }),
              }}
            />
          )}
        </ChartCard>
        <ChartCard
          title="Day of week"
          info="Share of each person's messages per weekday."
          query={q}
          empty={q.isSuccess && isEmptyChart(d?.weekdays)}
          data={d ? categoryTable(d.weekdays, "Weekday") : null}
          height={280}
        >
          {d && (
            <EChart
              height={280}
              option={categoryOption(d.weekdays, { format: pct })}
              onEvents={{
                click: (p: { dataIndex: number }) =>
                  open({ sender: d.person.person_id, weekday: p.dataIndex + 1 }),
              }}
            />
          )}
        </ChartCard>
      </div>

      <div className="card-grid">
        <section className="card flex flex-col gap-3 p-4">
          <h2 className="font-semibold">{d?.is_me ? "My stats" : "Their stats vs mine"}</h2>
          {d ? <StatsTable detail={d} /> : <Skeleton className="h-64 w-full" />}
          {d && !d.is_me && (
            <p className="text-xs text-muted">
              Measured in your shared chats. The better value is highlighted.
            </p>
          )}
        </section>
        <section className="card flex flex-col gap-3 p-4">
          <h2 className="font-semibold">{d?.is_me ? "My top emojis" : "Their top emojis"}</h2>
          {d ? <TopEmojis detail={d} /> : <Skeleton className="h-24 w-full" />}
          {d?.is_me && d.top_contacts && (
            <>
              <h2 className="mt-2 font-semibold">People I talk to most</h2>
              <TopContacts contacts={d.top_contacts} />
            </>
          )}
        </section>
      </div>

      <section className="card flex flex-col gap-3 p-4">
        <h2 className="font-semibold">{d?.is_me ? "All my chats" : "Chats in common"}</h2>
        {d ? (
          <ThreadsTable threads={d.threads} isMe={d.is_me} />
        ) : (
          <Skeleton className="h-40 w-full" />
        )}
      </section>
    </div>
  );
}
