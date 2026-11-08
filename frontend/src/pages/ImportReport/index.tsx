import { useMemo, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useApi } from "@/api/useApi";
import { ChartCard } from "@/charts/ChartCard";
import { EChart } from "@/charts/EChart";
import { recordsToTable } from "@/charts/csv";
import { DataTable } from "@/components/DataTable";
import { dataTableColumnHelper } from "@/components/dataTableColumns";
import { ErrorCard } from "@/components/ErrorCard";
import { Skeleton } from "@/components/Skeleton";
import { Tile, TileGrid } from "@/components/Tile";
import {
  describeOwnerMethod,
  durationRows,
  durationsOption,
  formatMsShort,
  normalizeReport,
  type ImportReport,
} from "@/features/import/report";
import { useOpenMessages } from "@/lib/drilldown";
import { formatDateTime, formatNumber } from "@/lib/format";

type KeyCount = ImportReport["unknown_keys"][number];
type TextCount = ImportReport["unmatched_system_like"][number];
type Failed = ImportReport["failed_threads"][number];

function Section({
  title,
  children,
  aside,
}: {
  title: string;
  children: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <section className="card flex min-w-0 flex-col gap-3 p-4" aria-label={title}>
      <header className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        {aside && <div className="ml-auto">{aside}</div>}
      </header>
      {children}
    </section>
  );
}

function KV({ rows }: { rows: Array<[ReactNode, ReactNode]> }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
      {rows.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="min-w-0 break-words tabular-nums">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function localeName(code: string): string {
  try {
    const name = new Intl.DisplayNames(undefined, { type: "language" }).of(code);
    return name && name !== code ? `${name} (${code})` : code;
  } catch {
    return code;
  }
}

const keyCol = dataTableColumnHelper<KeyCount>();
const keyColumns = keyCol.columns([
  keyCol.accessor("key", { header: "JSON key", cell: (c) => <code>{c.getValue()}</code> }),
  keyCol.accessor("count", { header: "Count", cell: (c) => formatNumber(c.getValue()) }),
]);

const textCol = dataTableColumnHelper<TextCount>();
const textColumns = textCol.columns([
  textCol.accessor("text", { header: "Message text" }),
  textCol.accessor("count", { header: "Count", cell: (c) => formatNumber(c.getValue()) }),
]);

const failCol = dataTableColumnHelper<Failed>();
const failColumns = failCol.columns([
  failCol.accessor("folder", { header: "Folder", cell: (c) => <code>{c.getValue()}</code> }),
  failCol.accessor("error", { header: "Error" }),
]);

/** Import report (§5.4). */
export default function ImportReportPage() {
  const q = useApi("/api/import/report", undefined, { filters: false });
  const open = useOpenMessages();
  const navigate = useNavigate();
  const r = useMemo(() => (q.data ? normalizeReport(q.data) : undefined), [q.data]);
  const durOption = useMemo(() => (r ? durationsOption(r.durations_ms) : {}), [r]);

  if (q.error)
    return (
      <div className="page">
        <h1 className="page-title">Import report</h1>
        <ErrorCard error={q.error} onRetry={() => void q.refetch()} />
      </div>
    );
  if (!r)
    return (
      <div className="page">
        <h1 className="page-title">Import report</h1>
        <Skeleton className="h-24" />
        <Skeleton className="h-64" />
      </div>
    );

  const locales = Object.entries(r.locales.detected).sort((a, b) => b[1] - a[1]);
  const types = Object.entries(r.message_types).sort((a, b) => b[1] - a[1]);
  const parseStats = Object.entries(r.parse_stats).sort((a, b) => a[0].localeCompare(b[0]));
  const durRows = durationRows(r.durations_ms);

  return (
    <div className="page">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="page-title">Import report</h1>
        <Link to="/import" className="btn btn-sm ml-auto">
          Re-import
        </Link>
      </div>
      <p className="text-sm text-muted">
        {r.imported_at ? `Imported ${formatDateTime(r.imported_at)}` : "Imported"}
        {r.kind &&
          ` · ${r.kind === "sample" ? "sample data" : r.kind === "upload" ? "uploaded zips" : "local path"}`}
        {r.total_ms != null && ` · took ${formatMsShort(r.total_ms)}`}
      </p>

      <TileGrid>
        <Tile
          label="Files"
          value={r.files}
          hint={r.zip_files.length ? `${r.zip_files.length} zip file(s)` : undefined}
        />
        <Tile
          label="Chats"
          value={r.threads}
          hint={`from ${formatNumber(r.thread_sources)} folders`}
          onClick={() => navigate("/chats")}
        />
        <Tile label="Messages" value={r.messages} onClick={() => open({})} />
        <Tile label="People" value={r.people} onClick={() => navigate("/people")} />
        <Tile label="Reactions" value={r.reactions} onClick={() => open({ has_reactions: true })} />
        <Tile
          label="Media files"
          value={r.media}
          hint={`${formatNumber(r.media_found)} found on disk`}
        />
      </TileGrid>

      <div className="card-grid">
        <Section title="Sources">
          <KV
            rows={[
              ["Export layouts", r.layouts.length ? r.layouts.join(", ") : "—"],
              ["Earliest message", formatDateTime(r.earliest)],
              ["Latest message", formatDateTime(r.latest)],
              ["Uploads kept", r.kept_uploads ? "yes (zips kept for media)" : "no"],
              ...r.sources.map((s, i): [ReactNode, ReactNode] => [
                i === 0 ? "Source" : "",
                <code key={s}>{s}</code>,
              ]),
              ...r.zip_files.map((z, i): [ReactNode, ReactNode] => [
                i === 0 ? "Zip files" : "",
                <code key={z}>{z}</code>,
              ]),
            ]}
          />
        </Section>

        <Section
          title="Owner (“me”)"
          aside={
            <Link to="/settings" className="btn btn-sm">
              Change
            </Link>
          }
        >
          <KV
            rows={[
              ["Owner", <strong key="o">{r.owner.name ?? "Not detected"}</strong>],
              ["How", describeOwnerMethod(r.owner.method)],
              ...(r.owner.method === "settings" && r.owner.auto_name
                ? [
                    [
                      "Auto-detected",
                      `${r.owner.auto_name} (${describeOwnerMethod(r.owner.auto_method)})`,
                    ] as [ReactNode, ReactNode],
                  ]
                : []),
              ...(r.owner.personal_information_name
                ? [
                    ["personal_information.json", r.owner.personal_information_name] as [
                      ReactNode,
                      ReactNode,
                    ],
                  ]
                : []),
            ]}
          />
          <p className="text-xs text-muted">All “me vs them” statistics depend on the owner.</p>
        </Section>

        <Section
          title="System-message languages"
          aside={
            <Link to="/settings" className="btn btn-sm">
              Change
            </Link>
          }
        >
          {locales.length === 0 ? (
            <p className="text-sm text-muted">No system messages matched any language.</p>
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="text-muted">
                <tr>
                  <th className="py-1 font-medium">Language</th>
                  <th className="py-1 text-right font-medium">Matches</th>
                  <th className="py-1 text-right font-medium">Enabled</th>
                </tr>
              </thead>
              <tbody>
                {locales.map(([code, n]) => (
                  <tr key={code} className="border-t border-border">
                    <td className="py-1">{localeName(code)}</td>
                    <td className="py-1 text-right tabular-nums">{formatNumber(n)}</td>
                    <td className="py-1 text-right">
                      {r.locales.enabled.includes(code) ? "✓" : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="text-xs text-muted">
            Mode: {r.locales.mode === "auto" ? "auto-detected" : "set in Settings"} · enabled:{" "}
            {r.locales.enabled.join(", ") || "—"}
            {r.locales.sample_size > 0 &&
              ` · sample of ${formatNumber(r.locales.sample_size)} messages`}
          </p>
        </Section>

        <Section title="Data clean-up">
          <KV
            rows={[
              ["Encoding fixes applied", formatNumber(r.encoding_fixes)],
              ["Duplicates removed", formatNumber(r.duplicates_removed)],
              ["Threads merged", formatNumber(r.threads_merged)],
              [
                "Empty messages",
                r.empty_messages > 0 ? (
                  <button
                    key="e"
                    type="button"
                    className="text-accent underline-offset-2 hover:underline"
                    onClick={() => open({ types: "unknown" })}
                  >
                    {formatNumber(r.empty_messages)}
                  </button>
                ) : (
                  "0"
                ),
              ],
              ["Failed chats", formatNumber(r.failed_threads.length)],
            ]}
          />
        </Section>

        <Section title="Message types">
          {types.length === 0 ? (
            <p className="text-sm text-muted">—</p>
          ) : (
            <KV
              rows={types.map(([t, n]): [ReactNode, ReactNode] => [
                t,
                <button
                  key={t}
                  type="button"
                  className="text-accent underline-offset-2 hover:underline"
                  onClick={() => open({ types: t })}
                >
                  {formatNumber(n)}
                </button>,
              ])}
            />
          )}
        </Section>

        <ChartCard
          title="Import duration per stage"
          info="Wall-clock time spent in each step of the import pipeline."
          empty={durRows.length === 0}
          data={recordsToTable(durRows, ["stage", { key: "ms", header: "milliseconds" }])}
          height={Math.max(160, durRows.length * 32 + 16)}
        >
          <EChart option={durOption} height={Math.max(160, durRows.length * 32 + 16)} />
        </ChartCard>

        <Section title={`Unknown JSON fields (${r.unknown_keys.length})`}>
          <p className="text-xs text-muted">
            Keys the parser did not recognize. They are ignored; many new keys can mean Instagram
            changed the export format.
          </p>
          <DataTable
            data={r.unknown_keys}
            columns={keyColumns}
            getRowId={(k) => k.key}
            initialSorting={[{ id: "count", desc: true }]}
            caption="Unknown JSON fields"
            empty="None — every field was recognized."
            className="max-h-80"
          />
        </Section>

        <Section title="Unmatched system-like messages (top 50)">
          <p className="text-xs text-muted">
            Messages that look like system messages (“… liked a message”, “… started a call”) but
            matched no pattern. They are counted as normal text. Click one to see where it occurs.
          </p>
          <DataTable
            data={r.unmatched_system_like}
            columns={textColumns}
            getRowId={(t, i) => `${i}:${t.text}`}
            initialSorting={[{ id: "count", desc: true }]}
            onRowClick={(t) => open({ text: t.text, text_mode: "plain" })}
            caption="Unmatched system-like messages"
            empty="None."
            className="max-h-80"
          />
        </Section>

        {r.failed_threads.length > 0 && (
          <Section title={`Failed chats (${r.failed_threads.length})`}>
            <DataTable
              data={r.failed_threads}
              columns={failColumns}
              getRowId={(f) => f.folder}
              caption="Failed chats"
              className="max-h-80"
            />
          </Section>
        )}

        {parseStats.length > 0 && (
          <Section title="Parser counters">
            <KV rows={parseStats.map(([k, v]): [ReactNode, ReactNode] => [k, formatNumber(v)])} />
          </Section>
        )}
      </div>
    </div>
  );
}
