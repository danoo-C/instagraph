import { useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import * as Tooltip from "@radix-ui/react-tooltip";
import type { EChartsType } from "echarts";
import { ChartSlotContext, type ChartSlot } from "@/charts/chartContext";
import { downloadCsv, type CsvTable } from "@/charts/csv";
import { exportChartPng } from "@/charts/exportPng";
import { ErrorCard } from "@/components/ErrorCard";
import { EmptyState } from "@/components/EmptyState";
import { ChartSkeleton } from "@/components/Skeleton";
import {
  ChartIcon,
  CloseIcon,
  DownloadIcon,
  ExpandIcon,
  ImageIcon,
  InfoIcon,
  TableIcon,
} from "@/components/icons";
import { useT } from "@/i18n";
import { cn, slugify } from "@/lib/utils";
import { useFilters } from "@/state/filters";
import { useResolvedTheme } from "@/state/theme";

const TABLE_ROW_LIMIT = 500;

/** Minimal shape of a TanStack Query result, so `query={useApi(...)}` just works. */
export interface QueryLike {
  isLoading?: boolean;
  isPending?: boolean;
  error?: unknown;
  refetch?: () => unknown;
}

export interface ChartCardProps {
  title: string;
  /** "What this means" text for the ⓘ tooltip. */
  info?: ReactNode;
  /** Data behind the chart: CSV export + "view as table". Omit to hide both. */
  data?: CsvTable | null;
  /** Shortcut for loading/error/retry from a useApi() result. */
  query?: QueryLike;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
  /** Show the empty state instead of children. */
  empty?: boolean;
  emptyMessage?: ReactNode;
  /** Extra controls in the header (e.g. day/week/month toggle). */
  actions?: ReactNode;
  /** Skeleton height while loading. Default 320. */
  height?: number | string;
  /** File name base for exports (default: slug of title). */
  filename?: string;
  className?: string;
  /** Usually an <EChart/>; any content works (PNG export needs an EChart). */
  children: ReactNode;
}

/**
 * Card wrapper for every chart (§5.1, §11, §12): title, ⓘ tooltip, PNG (2×, themed, title +
 * date range), CSV, fullscreen, "view as table", loading skeleton, error card.
 */
export function ChartCard({
  title,
  info,
  data,
  query,
  loading,
  error,
  onRetry,
  empty,
  emptyMessage,
  actions,
  height = 320,
  filename,
  className,
  children,
}: ChartCardProps) {
  const t = useT();
  const theme = useResolvedTheme();
  const { rangeLabel } = useFilters();
  const [showTable, setShowTable] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const chartRef = useRef<EChartsType | null>(null);
  const [hasChart, setHasChart] = useState(false);

  const isLoading = loading ?? (query ? Boolean(query.isLoading) : false);
  const err = error ?? query?.error ?? null;
  const retry = onRetry ?? (query?.refetch ? () => void query.refetch?.() : undefined);
  const base = filename ?? slugify(title);

  const register = useCallback((chart: EChartsType | null) => {
    chartRef.current = chart;
    setHasChart(chart != null);
  }, []);
  const slot = useMemo<ChartSlot>(
    () => ({ register, label: title, fullscreen: false }),
    [register, title],
  );
  const fullSlot = useMemo<ChartSlot>(
    () => ({ register: () => {}, label: title, fullscreen: true }),
    [title],
  );

  const canTable = Boolean(data && data.headers.length);
  const ready = !isLoading && !err;

  let body: ReactNode;
  if (isLoading) body = <ChartSkeleton height={height} />;
  else if (err) body = <ErrorCard error={err} onRetry={retry} />;
  else if (empty)
    body = <EmptyState title={emptyMessage ?? t("chart.noData")} className="min-h-40" />;
  else if (showTable && data) body = <DataPreview data={data} caption={title} />;
  else body = children;

  return (
    <section className={cn("card flex min-w-0 flex-col gap-3 p-4", className)} aria-label={title}>
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        {info && (
          <Tooltip.Root>
            <Tooltip.Trigger asChild>
              <button type="button" className="icon-btn" aria-label={t("chart.info")}>
                <InfoIcon />
              </button>
            </Tooltip.Trigger>
            <Tooltip.Portal>
              <Tooltip.Content className="tooltip max-w-xs" sideOffset={4}>
                {info}
                <Tooltip.Arrow className="fill-surface" />
              </Tooltip.Content>
            </Tooltip.Portal>
          </Tooltip.Root>
        )}
        <div className="ml-auto flex items-center gap-1">
          {actions}
          {canTable && (
            <button
              type="button"
              className="icon-btn"
              aria-pressed={showTable}
              aria-label={showTable ? t("chart.viewChart") : t("chart.viewTable")}
              title={showTable ? t("chart.viewChart") : t("chart.viewTable")}
              onClick={() => setShowTable((v) => !v)}
              disabled={!ready}
            >
              {showTable ? <ChartIcon /> : <TableIcon />}
            </button>
          )}
          <button
            type="button"
            className="icon-btn"
            aria-label={t("chart.exportPng")}
            title={t("chart.exportPng")}
            disabled={!ready || showTable || !hasChart}
            onClick={() => {
              if (chartRef.current)
                void exportChartPng(chartRef.current, {
                  title,
                  subtitle: rangeLabel,
                  theme,
                  filename: base,
                });
            }}
          >
            <ImageIcon />
          </button>
          {canTable && (
            <button
              type="button"
              className="icon-btn"
              aria-label={t("chart.exportCsv")}
              title={t("chart.exportCsv")}
              disabled={!ready}
              onClick={() => data && downloadCsv(base, data)}
            >
              <DownloadIcon />
            </button>
          )}
          <Dialog.Root open={fullscreen} onOpenChange={setFullscreen}>
            <Dialog.Trigger asChild>
              <button
                type="button"
                className="icon-btn"
                aria-label={t("chart.fullscreen")}
                title={t("chart.fullscreen")}
                disabled={!ready}
              >
                <ExpandIcon />
              </button>
            </Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Overlay className="fixed inset-0 z-40 bg-black/50" />
              <Dialog.Content className="card fixed inset-2 z-50 flex flex-col gap-3 p-4 sm:inset-6">
                <div className="flex items-center gap-2">
                  <Dialog.Title className="font-semibold">{title}</Dialog.Title>
                  <Dialog.Description className="text-sm text-muted">
                    {rangeLabel}
                  </Dialog.Description>
                  <Dialog.Close asChild>
                    <button
                      type="button"
                      className="icon-btn ml-auto"
                      aria-label={t("chart.close")}
                    >
                      <CloseIcon />
                    </button>
                  </Dialog.Close>
                </div>
                <div className="min-h-0 flex-1 overflow-auto">
                  <ChartSlotContext.Provider value={fullSlot}>
                    {showTable && data ? <DataPreview data={data} caption={title} /> : children}
                  </ChartSlotContext.Provider>
                </div>
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
        </div>
      </header>
      <ChartSlotContext.Provider value={slot}>
        <div className="min-w-0">{body}</div>
      </ChartSlotContext.Provider>
    </section>
  );
}

/** Accessible plain-table rendering of the chart data ("view as table"). */
function DataPreview({ data, caption }: { data: CsvTable; caption: string }) {
  const t = useT();
  const rows = data.rows.slice(0, TABLE_ROW_LIMIT);
  return (
    <div className="max-h-96 overflow-auto rounded-md border border-border">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 bg-surface-2">
          <tr>
            {data.headers.map((h) => (
              <th key={h} scope="col" className="px-3 py-2 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-border">
              {r.map((c, j) => (
                <td key={j} className="px-3 py-1.5 tabular-nums">
                  {c instanceof Date ? c.toISOString() : String(c ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {data.rows.length > TABLE_ROW_LIMIT && (
        <p className="p-2 text-xs text-muted">{t("chart.rowsTruncated", { n: TABLE_ROW_LIMIT })}</p>
      )}
    </div>
  );
}
