import { categoryTable, timeSeriesTable } from "@/charts/tables";
import { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useApi, useApiInfinite } from "@/api/useApi";
import type { components } from "@/api/schema";
import { ChartCard } from "@/charts/ChartCard";
import { recordsToTable } from "@/charts/csv";
import { EChart } from "@/charts/EChart";
import type { EChartsOption } from "@/charts/registerEcharts";
import { defaultGrid, timeSeriesZoom } from "@/charts/sampling";
import { DataTable } from "@/components/DataTable";
import { dataTableColumnHelper } from "@/components/dataTableColumns";
import { ErrorCard } from "@/components/ErrorCard";
import { PersonBadge } from "@/components/PersonBadge";
import { Skeleton } from "@/components/Skeleton";
import { Tile, TileGrid } from "@/components/Tile";
import {
  bucketEnd,
  categoryToEcharts,
  isEmptyChart,
  timeSeriesToEcharts,
} from "@/features/tabs/charts";
import { useDrill, useOpenContext } from "@/lib/drilldown";
import { chunkRows, galleryLayout, MEDIA_KIND_LABEL } from "@/features/tabs/media";
import { MediaLightbox } from "@/features/tabs/mediaLightbox";
import type { TimeSeries } from "@/features/tabs/types";
import { SelectInput } from "@/components/SelectInput";
import { TabState } from "@/features/tabs/ui";
import { formatDuration, formatNumber, formatPercent } from "@/lib/format";
import { useFilters } from "@/state/filters";
import type { ChatTabProps } from "@/pages/ChatDetail/tabs";

type S = components["schemas"];
type ChatMedia = S["ChatMediaResponse"];
type RankedItem = S["RankedItem"];
type MediaPersonCounts = S["MediaPersonCounts"];
type GalleryItem = S["instagraph__schemas__media__MediaItem"];

const PATH = "/api/chats/{chat_id}/media";

export default function MediaTab({ chatId }: ChatTabProps) {
  const id = Number(chatId);
  const [poll, setPoll] = useState(false);
  const q = useApi(PATH, { path: { chat_id: id } }, { refetchInterval: poll ? 3000 : false });
  const status = q.data?.voice_notes.status;
  // Poll while voice-note durations are still being read (derived state, set during render).
  if ((status === "pending") !== poll) setPoll(status === "pending");

  return (
    <TabState query={q}>
      {() => {
        const d = q.data!;
        return (
          <div className="flex flex-col gap-4">
            <div className="card-grid">
              <CountsChart data={d} />
              <TypeMix data={d} />
              <ReelsExchange data={d} />
              <VoiceNotes data={d} />
              <Ranked
                title="Top shared accounts"
                info="Accounts whose posts, reels and profiles were shared most often in this chat."
                items={d.top_shared_accounts}
                header="account"
              />
              <Ranked
                title="Top link domains"
                info="Domains of external links (URLs in messages and external shares)."
                items={d.top_link_domains}
                header="domain"
              />
            </div>
            <PerPersonTable rows={d.per_person} />
            <Gallery chatId={id} data={d} />
          </div>
        );
      }}
    </TabState>
  );
}

function CountsChart({ data }: { data: ChatMedia }) {
  const drill = useDrill();
  const chart = data.counts_chart;
  const option: EChartsOption = {
    grid: defaultGrid({ legend: true }),
    legend: { type: "scroll", top: 0 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "category", data: chart.categories, axisLabel: { interval: 0, rotate: 30 } },
    yAxis: { type: "value" },
    series: categoryToEcharts(chart),
  } as EChartsOption;
  return (
    <ChartCard
      title="Media per person"
      info="Photos, videos, voice notes, GIFs, stickers and shares sent by each person. Click a bar to see those messages."
      data={categoryTable(chart, "type")}
      empty={isEmptyChart(chart)}
    >
      <EChart
        option={option}
        onEvents={{
          click: (p) => {
            const s = chart.series[p.seriesIndex];
            drill(chart.drilldowns?.[p.dataIndex] ?? { thread_id: data.chat_id }, {
              ...(s?.person_id != null ? { sender_id: s.person_id } : {}),
            });
          },
        }}
      />
    </ChartCard>
  );
}

function timeDrill(
  drill: ReturnType<typeof useDrill>,
  chatId: number,
  ts: TimeSeries,
  dataIndex: number,
  extra: Record<string, string | number> = {},
) {
  const x = ts.x[dataIndex];
  if (x) drill({ thread_id: chatId, start: x, end: bucketEnd(x, ts.bucket) }, extra);
}

function TypeMix({ data }: { data: ChatMedia }) {
  const drill = useDrill();
  const ts = data.type_mix;
  const option: EChartsOption = {
    grid: defaultGrid({ slider: true, legend: true }),
    legend: { type: "scroll", top: 0 },
    tooltip: {
      trigger: "axis",
      valueFormatter: (v) => formatPercent(typeof v === "number" ? v : null),
    },
    dataZoom: timeSeriesZoom(),
    xAxis: { type: "time" },
    yAxis: {
      type: "value",
      max: 1,
      axisLabel: { formatter: (v: number) => formatPercent(v, 0) },
    },
    series: timeSeriesToEcharts(ts, { type: "line", stack: "mix", area: true }),
  } as EChartsOption;
  return (
    <ChartCard
      title="Message type mix over time"
      info={`Share of messages per ${ts.bucket} that are text, photo, video, audio, share or other (sums to 100%).`}
      data={timeSeriesTable(ts)}
      empty={isEmptyChart(ts)}
    >
      <EChart
        option={option}
        onEvents={{
          click: (p) => {
            const key = ts.series[p.seriesIndex]?.key;
            const types =
              key && ["text", "photo", "video", "audio", "share"].includes(key) ? key : undefined;
            timeDrill(drill, data.chat_id, ts, p.dataIndex, types ? { types } : {});
          },
        }}
      />
    </ChartCard>
  );
}

function ReelsExchange({ data }: { data: ChatMedia }) {
  const drill = useDrill();
  const ts = data.reels_exchange;
  const option: EChartsOption = {
    grid: defaultGrid({ slider: true, legend: true }),
    legend: { type: "scroll", top: 0 },
    tooltip: { trigger: "axis" },
    dataZoom: timeSeriesZoom(),
    xAxis: { type: "time" },
    yAxis: { type: "value" },
    series: timeSeriesToEcharts(ts, { type: "line" }),
  } as EChartsOption;
  return (
    <ChartCard
      title="Reel exchange"
      info={`Reels and posts shared by each person per ${ts.bucket}.`}
      data={timeSeriesTable(ts)}
      empty={isEmptyChart(ts)}
    >
      <EChart
        option={option}
        onEvents={{
          click: (p) => {
            const s = ts.series[p.seriesIndex];
            timeDrill(drill, data.chat_id, ts, p.dataIndex, {
              types: "share",
              ...(s?.person_id != null ? { sender_id: s.person_id } : {}),
            });
          },
        }}
      />
    </ChartCard>
  );
}

function Ranked({
  title,
  info,
  items,
  header,
}: {
  title: string;
  info: string;
  items: RankedItem[];
  header: string;
}) {
  const drill = useDrill();
  const top = items.slice(0, 20);
  const option: EChartsOption = {
    grid: defaultGrid(),
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "value" },
    yAxis: {
      type: "category",
      inverse: true,
      data: top.map((i) => i.key),
      axisLabel: { width: 140, overflow: "truncate" },
    },
    series: [{ type: "bar", name: "Count", data: top.map((i) => i.count) }],
  };
  return (
    <ChartCard
      title={title}
      info={info}
      data={recordsToTable(items, [
        { key: "key", header },
        { key: "count", header: "count" },
      ])}
      empty={items.length === 0}
    >
      <EChart
        option={option}
        height={Math.max(200, top.length * 24 + 40)}
        onEvents={{ click: (p) => drill(top[p.dataIndex]?.drilldown) }}
      />
    </ChartCard>
  );
}

function VoiceNotes({ data }: { data: ChatMedia }) {
  const drill = useDrill();
  const v = data.voice_notes;
  const sec = (ms: number | null | undefined) => (ms == null ? null : ms / 1000);
  return (
    <ChartCard
      title="Voice notes"
      info="Number of voice notes and, when the audio files are in the export, their total and average length."
      data={recordsToTable(
        v.per_person.map((p) => ({
          name: p.person.name,
          count: p.count,
          with_duration: p.with_duration,
          total_s: sec(p.total_ms),
          avg_s: sec(p.avg_ms),
        })),
      )}
      empty={v.count === 0}
      emptyMessage="No voice notes in this period."
      height={200}
    >
      <div className="flex flex-col gap-3">
        {v.status === "pending" && (
          <p className="text-sm text-muted" role="status">
            Reading voice note durations in the background… this updates automatically.
          </p>
        )}
        {v.status === "unavailable" && (
          <p className="text-sm text-muted">
            The audio files aren't in the export, so only counts are available.
          </p>
        )}
        <TileGrid className="!grid-cols-3">
          <Tile
            label="Voice notes"
            value={v.count}
            onClick={() => drill({ thread_id: data.chat_id, types: ["audio"] })}
          />
          <Tile
            label="Total length"
            value={sec(v.total_ms)}
            format="duration"
            loading={v.status === "pending" && v.total_ms == null}
          />
          <Tile
            label="Average"
            value={sec(v.avg_ms)}
            format="duration"
            loading={v.status === "pending" && v.avg_ms == null}
          />
        </TileGrid>
        <ul className="flex flex-col gap-1 text-sm">
          {v.per_person.map((p) => (
            <li key={p.person.person_id}>
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded px-1 py-1 text-left hover:bg-surface-2"
                onClick={() => drill(p.drilldown)}
              >
                <PersonBadge
                  name={p.person.name}
                  id={p.person.person_id}
                  color_index={p.person.color_index}
                  showName
                  className="min-w-0 flex-1"
                />
                <span className="tabular-nums">{formatNumber(p.count)}</span>
                <span className="w-28 text-right tabular-nums text-muted">
                  {p.total_ms != null ? formatDuration(p.total_ms / 1000) : "—"}
                </span>
                <span className="w-20 text-right tabular-nums text-muted">
                  {p.avg_ms != null ? `⌀ ${formatDuration(p.avg_ms / 1000)}` : ""}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </ChartCard>
  );
}

const col = dataTableColumnHelper<MediaPersonCounts>();
const COUNT_KEYS = [
  ["photos", "Photos"],
  ["videos", "Videos"],
  ["voice_notes", "Voice notes"],
  ["gifs", "GIFs"],
  ["stickers", "Stickers"],
  ["shared_posts", "Posts"],
  ["shared_reels", "Reels"],
  ["shared_stories", "Stories"],
  ["shared_profiles", "Profiles"],
  ["shared_other", "Other shares"],
  ["external_links", "Links"],
  ["unsent", "Unsent"],
] as const;

function PerPersonTable({ rows }: { rows: MediaPersonCounts[] }) {
  const drill = useDrill();
  const columns = useMemo(
    () =>
      col.columns([
        col.accessor((r) => r.person.name, {
          id: "person",
          header: "Person",
          cell: (c) => (
            <PersonBadge
              name={c.row.original.person.name}
              id={c.row.original.person.person_id}
              color_index={c.row.original.person.color_index}
              showName
            />
          ),
        }),
        ...COUNT_KEYS.map(([k, label]) =>
          col.accessor(k, { header: label, cell: (c) => formatNumber(c.getValue() as number) }),
        ),
      ]),
    [],
  );
  return (
    <ChartCard
      title="Media & shares per person"
      info="All media, share and link counts per person. Click a row to see that person's messages."
      data={recordsToTable(
        rows.map((r) => ({
          person: r.person.name,
          ...Object.fromEntries(COUNT_KEYS.map(([k]) => [k, r[k]])),
        })),
      )}
      empty={rows.length === 0}
      height={160}
    >
      <DataTable
        data={rows}
        columns={columns}
        getRowId={(r) => String(r.person.person_id)}
        onRowClick={(r) => drill(r.drilldown)}
        caption="Media and shares per person"
      />
    </ChartCard>
  );
}

// ------------------------------------------------------------------ gallery

const GAP = 8;

function Gallery({ chatId, data }: { chatId: number; data: ChatMedia }) {
  const { filters } = useFilters();
  const openContext = useOpenContext();
  const [sender, setSender] = useState("");
  const [year, setYear] = useState("");
  const g = useApiInfinite<ChatMedia>(
    PATH,
    {
      path: { chat_id: chatId },
      query: { sender: sender || undefined, year: year || undefined, limit: 60 },
    },
    { getNextCursor: (p) => p.gallery.next_cursor },
  );
  const items = useMemo<GalleryItem[]>(
    () => g.data?.pages.flatMap((p) => p.gallery.items) ?? [],
    [g.data],
  );
  const total = g.data?.pages[0]?.gallery.total ?? data.gallery.total;
  const years = data.gallery.years;

  const scrollRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => e && setWidth(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const { columns, cell } = galleryLayout(width - 4, 120, GAP);
  const rows = useMemo(() => chunkRows(items, columns), [items, columns]);

  // eslint-disable-next-line react-hooks/incompatible-library -- virtualizer used only in this render
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => cell + GAP,
    overscan: 4,
  });
  useEffect(() => virtualizer.measure(), [cell, virtualizer]);
  const vItems = virtualizer.getVirtualItems();
  const last = vItems.length ? vItems[vItems.length - 1]!.index : -1;
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = g;
  useEffect(() => {
    if (last >= rows.length - 3 && hasNextPage && !isFetchingNextPage) void fetchNextPage();
  }, [last, rows.length, hasNextPage, isFetchingNextPage, fetchNextPage]);

  const [open, setOpen] = useState<number | null>(null);
  const lightboxItems = useMemo(
    () =>
      items.map((i) => ({
        media_id: i.media_id,
        kind: i.kind,
        url: i.url,
        exists: i.exists,
        ts: i.ts,
        senderName: i.sender.name,
        message_id: i.message_id,
      })),
    [items],
  );

  return (
    <section className="card flex flex-col gap-3 p-4" aria-label="Photo and video gallery">
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">Photos & videos</h3>
        <span className="text-xs text-muted">{formatNumber(total)} items</span>
        <div className="ml-auto flex flex-wrap gap-2">
          <SelectInput label="Sender" value={sender} onChange={setSender}>
            <option value="">Everyone</option>
            {data.per_person.map((p) => (
              <option key={p.person.person_id} value={p.person.person_id}>
                {p.person.name}
              </option>
            ))}
          </SelectInput>
          <SelectInput label="Year" value={year} onChange={setYear}>
            <option value="">All years</option>
            {years.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </SelectInput>
        </div>
      </header>
      {g.error && !g.data ? (
        <ErrorCard error={g.error} onRetry={() => void g.refetch()} />
      ) : (
        <div
          ref={scrollRef}
          className="relative h-[560px] overflow-auto rounded-md"
          role="list"
          aria-label="Gallery"
          aria-busy={g.isFetching}
        >
          {g.isLoading ? (
            <Skeleton className="h-full w-full" />
          ) : items.length === 0 ? (
            <p className="p-8 text-center text-sm text-muted">
              No photos or videos for this selection.
            </p>
          ) : (
            <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
              {vItems.map((v) => (
                <div
                  key={v.key}
                  className="absolute left-0 flex"
                  style={{ top: v.start, gap: GAP, height: cell }}
                >
                  {rows[v.index]!.map((it, j) => {
                    const idx = v.index * columns + j;
                    return (
                      <button
                        key={it.media_id}
                        type="button"
                        role="listitem"
                        onClick={() => setOpen(idx)}
                        className="group relative overflow-hidden rounded-md bg-surface-2 focus-visible:ring-2 focus-visible:ring-accent"
                        style={{ width: cell, height: cell }}
                        aria-label={`${MEDIA_KIND_LABEL[it.kind] ?? it.kind} from ${it.sender.name}`}
                        title={`${it.sender.name}`}
                      >
                        {it.exists && it.thumb_url ? (
                          <img
                            src={it.thumb_url}
                            alt=""
                            loading="lazy"
                            className="h-full w-full object-cover transition-transform group-hover:scale-105"
                          />
                        ) : (
                          <span className="flex h-full w-full flex-col items-center justify-center gap-1 text-xs text-muted">
                            <span className="text-2xl" aria-hidden="true">
                              {it.kind === "video" ? "🎬" : "🖼️"}
                            </span>
                            Not in export
                          </span>
                        )}
                        {it.kind === "video" && (
                          <span className="absolute bottom-1 right-1 rounded bg-black/60 px-1 text-xs text-white">
                            ▶
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {isFetchingNextPage && <p className="text-xs text-muted">Loading more…</p>}
      <MediaLightbox
        items={lightboxItems}
        index={open}
        onIndexChange={setOpen}
        timeZone={filters.tz}
        onShowMessage={(it) => {
          const m = items.find((x) => x.media_id === it.media_id);
          if (m) openContext(m.message_id, chatId);
        }}
      />
    </section>
  );
}
