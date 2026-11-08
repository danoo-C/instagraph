import * as Popover from "@radix-ui/react-popover";
import * as Switch from "@radix-ui/react-switch";
import * as Tooltip from "@radix-ui/react-tooltip";
import { ChevronDownIcon, CloseIcon, GlobeIcon } from "@/components/icons";
import { useT, type TranslationKey } from "@/i18n";
import {
  browserTimeZone,
  presetToValue,
  useFilters,
  valueToPreset,
  type Filters,
} from "@/state/filters";

const FIRST_YEAR = 2013; // Instagram Direct launched in Dec 2013

/**
 * Years offered as presets, newest first. Pass dataset bounds when known; a year that is
 * currently selected (e.g. from a shared URL) is always included.
 */
function yearOptions(
  firstYear = FIRST_YEAR,
  lastYear = new Date().getFullYear(),
  selected?: number,
): number[] {
  const lo = Math.min(firstYear, lastYear, selected ?? Infinity);
  const hi = Math.max(firstYear, lastYear, selected ?? -Infinity);
  const out: number[] = [];
  for (let y = hi; y >= lo; y--) out.push(y);
  return out;
}

type BoolKey = "include_groups" | "include_requests" | "include_archived" | "exclude_system";
const TOGGLES: Array<[BoolKey, TranslationKey]> = [
  ["include_groups", "filters.includeGroups"],
  ["include_requests", "filters.includeRequests"],
  ["include_archived", "filters.includeArchived"],
  ["exclude_system", "filters.excludeSystem"],
];

export interface FilterBarProps {
  /** Dataset year bounds for the year presets (Layout passes them from `GET /api/status`;
   *  fallback 2013 … current year while unknown). */
  firstYear?: number;
  lastYear?: number;
}

/** Global filter controls (§5.1). State lives in the URL via useFilters(). */
export function FilterBar({ firstYear, lastYear }: FilterBarProps) {
  const t = useT();
  const { filters, setFilters, preset, setPreset, resetFilters, isDefault } = useFilters();
  const presetValue = presetToValue(preset);
  const changedToggles = TOGGLES.filter(([k]) => filters[k] !== defaultsFor(k)).length;

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
      <label className="sr-only" htmlFor="filter-range">
        {t("filters.range")}
      </label>
      <select
        id="filter-range"
        className="input h-8 py-0"
        value={presetValue}
        onChange={(e) => {
          const p = valueToPreset(e.target.value);
          if (p.kind === "custom") {
            // Keep current bounds; seed with something editable if all-time.
            if (!filters.start && !filters.end) setPreset({ kind: "last12m" });
          } else setPreset(p);
        }}
      >
        <option value="all">{t("filters.allTime")}</option>
        <option value="last12m">{t("filters.last12m")}</option>
        <option value="last30d">{t("filters.last30d")}</option>
        {yearOptions(firstYear, lastYear, preset.kind === "year" ? preset.year : undefined).map(
          (y) => (
            <option key={y} value={`year:${y}`}>
              {y}
            </option>
          ),
        )}
        <option value="custom">{t("filters.custom")}</option>
      </select>

      {preset.kind === "custom" && (
        <span className="flex items-center gap-1">
          <label className="sr-only" htmlFor="filter-start">
            {t("filters.from")}
          </label>
          <input
            id="filter-start"
            type="date"
            className="input h-8 py-0"
            value={filters.start ?? ""}
            max={filters.end ?? undefined}
            onChange={(e) => setFilters({ start: e.target.value || null })}
          />
          <span aria-hidden="true">–</span>
          <label className="sr-only" htmlFor="filter-end">
            {t("filters.to")}
          </label>
          <input
            id="filter-end"
            type="date"
            className="input h-8 py-0"
            value={filters.end ?? ""}
            min={filters.start ?? undefined}
            onChange={(e) => setFilters({ end: e.target.value || null })}
          />
        </span>
      )}

      <Popover.Root>
        <Popover.Trigger asChild>
          <button type="button" className="btn btn-sm">
            {t("filters.chats")}
            {changedToggles > 0 && (
              <span className="rounded-full bg-accent px-1.5 text-xs text-accent-fg">
                {changedToggles}
              </span>
            )}
            <ChevronDownIcon />
          </button>
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content
            className="card z-50 flex w-72 flex-col gap-3 p-3"
            sideOffset={6}
            align="start"
          >
            {TOGGLES.map(([key, label]) => (
              <ToggleRow
                key={key}
                id={`filter-${key}`}
                label={t(label)}
                checked={filters[key]}
                onChange={(v) => setFilters({ [key]: v } as Partial<Filters>)}
              />
            ))}
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>

      {filters.people.length > 0 && (
        <span className="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2 py-1 text-xs">
          {t("filters.people", { n: filters.people.length })}
          <button
            type="button"
            className="icon-btn h-4 w-4"
            aria-label={t("filters.clearPeople")}
            onClick={() => setFilters({ people: [] })}
          >
            <CloseIcon />
          </button>
        </span>
      )}

      {!isDefault && (
        <button
          type="button"
          className="text-xs text-muted underline-offset-2 hover:underline"
          onClick={resetFilters}
        >
          {t("filters.reset")}
        </button>
      )}

      <TimeZoneIndicator tz={filters.tz} />
    </div>
  );
}

function defaultsFor(k: BoolKey): boolean {
  return k !== "include_requests";
}

function ToggleRow({
  id,
  label,
  checked,
  onChange,
}: {
  id: string;
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <label htmlFor={id} className="text-sm">
        {label}
      </label>
      <Switch.Root
        id={id}
        checked={checked}
        onCheckedChange={onChange}
        className="relative h-5 w-9 shrink-0 rounded-full bg-border transition-colors data-[state=checked]:bg-accent"
      >
        <Switch.Thumb className="block h-4 w-4 translate-x-0.5 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-[18px]" />
      </Switch.Root>
    </div>
  );
}

/** Shows the active time zone; highlighted when it differs from the browser's. */
export function TimeZoneIndicator({ tz }: { tz: string }) {
  const t = useT();
  const differs = tz !== browserTimeZone();
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <span
          tabIndex={0}
          className={
            "ml-auto inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs " +
            (differs
              ? "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-100"
              : "text-muted")
          }
          aria-label={`${t("filters.timezone")}: ${tz}`}
        >
          <GlobeIcon />
          <span className="max-w-[10rem] truncate">{tz}</span>
        </span>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content className="tooltip" sideOffset={4}>
          {t("filters.timezone")}: {tz}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}
