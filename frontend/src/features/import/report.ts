import type { components } from "@/api/schema";
import type { EChartsOption } from "@/charts/registerEcharts";
import { personColor } from "@/lib/colors";
import { formatDuration } from "@/lib/format";

type S = components["schemas"];
type RawReport = S["ImportReport"];

/** Import report with every server-side default filled in (OpenAPI marks them optional). */
export type ImportReport = Omit<Required<RawReport>, "owner" | "locales"> & {
  owner: Required<S["OwnerInfo"]>;
  locales: Required<S["LocaleInfo"]>;
};

export function normalizeReport(r: RawReport): ImportReport {
  return {
    import_id: null,
    imported_at: null,
    kind: null,
    sources: [],
    zip_files: [],
    layouts: [],
    unknown_keys: [],
    unmatched_system_like: [],
    message_types: {},
    parse_stats: {},
    failed_threads: [],
    earliest: null,
    latest: null,
    durations_ms: {},
    total_ms: null,
    ...r,
    owner: {
      name: null,
      person_id: null,
      method: "none",
      auto_name: null,
      auto_method: "none",
      personal_information_name: null,
      ...r.owner,
    },
    locales: {
      detected: {},
      auto_enabled: [],
      enabled: [],
      mode: "auto",
      sample_size: 0,
      ...r.locales,
    },
  };
}

const STAGE_ORDER = ["sample", "discovery", "locales", "parse", "merge", "derive", "persist"];

/** durations_ms → rows in pipeline order (unknown stages last, alphabetical). */
export function durationRows(
  durations: Record<string, number>,
): Array<{ stage: string; ms: number }> {
  const rank = (s: string) => {
    const i = STAGE_ORDER.indexOf(s);
    return i < 0 ? STAGE_ORDER.length : i;
  };
  return Object.entries(durations)
    .map(([stage, ms]) => ({ stage, ms }))
    .sort((a, b) => rank(a.stage) - rank(b.stage) || a.stage.localeCompare(b.stage));
}

export const formatMsShort = (ms: number) =>
  ms < 1000 ? `${Math.round(ms)} ms` : formatDuration(ms / 1000);

export function durationsOption(durations: Record<string, number>): EChartsOption {
  const rows = durationRows(durations);
  return {
    grid: { left: 8, right: 72, top: 8, bottom: 8, containLabel: true },
    tooltip: { trigger: "item", valueFormatter: (v) => formatMsShort(v as number) },
    xAxis: { type: "value", axisLabel: { formatter: (v: number) => formatMsShort(v) } },
    yAxis: { type: "category", inverse: true, data: rows.map((r) => r.stage) },
    series: [
      {
        type: "bar",
        color: personColor(0),
        data: rows.map((r) => r.ms),
        label: {
          show: true,
          position: "right",
          formatter: (p: { value?: unknown }) => formatMsShort(Number(p.value)),
        },
      },
    ],
  };
}

const OWNER_METHODS: Record<string, string> = {
  settings: "set by you in Settings",
  personal_information: "from personal_information.json in the export",
  most_threads: "the participant who appears in the most chats",
  none: "could not be detected",
};

export function describeOwnerMethod(method: string | null | undefined): string {
  return OWNER_METHODS[method ?? "none"] ?? method ?? "unknown";
}
