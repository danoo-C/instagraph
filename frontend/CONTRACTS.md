# Frontend contracts (for page authors)

Shell, shared components and conventions are in place. Pages are placeholders. Fill them in without changing shared APIs. If a shared API really has to change, keep it backwards compatible.

## Rules

- **Absolute imports only:** `import { ChartCard } from "@/charts/ChartCard"`. `./` and `../` fail ESLint (§8.6).
- **No `dangerouslySetInnerHTML`.** Render message text as text (`MessageBubble`, `highlightText`).
- **No external resources:** no CDNs, fonts or analytics (CSP `default-src 'self'`).
- **Strings:** add keys to `src/i18n/en.ts` and use `const t = useT(); t("key", { n: 3 })`. Adding them to `sk.ts` is optional.
- **Formatting:** use `@/lib/format` for numbers, dates and durations, never hand-rolled code. Ratios are 0..1 and durations are in seconds.
- **Charts and tables:** every chart goes inside a `ChartCard`. Every count is clickable and opens the Message Browser (`Tile query=…` or `useOpenMessages`).

## Folders

```
src/pages/<Name>/index.tsx          default-export page component (lazy-loaded route)
src/pages/<Name>/*.tsx              page-private components (charts, sections)
src/pages/ChatDetail/tabs/<Tab>.tsx one file per chat tab, receives { chatId }; registry in tabs.ts
src/components/                     shared UI (Tile, DataTable, PersonBadge, MessageBubble, ErrorCard, Skeleton, EmptyState)
src/charts/                         ChartCard, EChart, theme, csv, sampling helpers
src/api/                            client, useApi, errors, schema.d.ts (generated)
src/state/                          filters (URL), theme, ui (zustand)
src/lib/                            colors, format, drilldown, utils, highlight
```

Routes: `/import`, `/import/report`, `/overview`, `/chats`, `/chats/:id/:tab?`, `/people`, `/people/:id`, `/compare`, `/wrapped`, `/search`, `/settings`. Each page is a separate chunk.

## Calling the API

```tsx
import { useApi } from "@/api/useApi";

// Typed once `npm run gen-types` has produced schema.d.ts; until then pass the type:
const q = useApi<OverviewTiles>("/api/overview");
const tab = useApi<ChatWhen>("/api/chats/{id}/{tab}", { path: { id: chatId, tab: "when" } });
const extra = useApi<Terms>("/api/chats/{id}/terms", { path: { id }, query: { q: "love" } });
const status = useApi<Status>("/api/status", undefined, { filters: false });
```

- Signature: `useApi(path, params?: { path?, query? }, options?: { filters?=true, enabled?, staleTime?, keepPrevious?=true, refetchInterval? })`. It returns a TanStack `UseQueryResult<T, ApiError>`.
- Global filters (`start, end, tz, include_*, exclude_system, people`) are merged into the query automatically. `params.query` wins over them.
- Errors are `ApiError { status, code, message }`, parsed from `{ error: { code, message } }`. A **409** sends the user to `/import`. 4xx responses are not retried.
- If a response has a `dataset_version` that differs from the last one seen, **all queries are invalidated**.
- Cursor pagination: `useApiInfinite<T>(path, params, { getNextCursor: (page) => page.next_cursor })` returns pages in `data.pages`.
- Mutations: use `apiFetch<T>("POST" | "PUT" | "DELETE", path, { path, query, body })` with TanStack `useMutation`. Then invalidate with `queryClient.invalidateQueries({ queryKey: ["api", "/api/settings"] })` (from `@/api/queryClient`). Query keys are `["api", path, pathParams, query]`.

## Filters

```ts
const { filters, setFilters, resetFilters, toQuery, preset, setPreset, rangeLabel, isDefault } =
  useFilters();
```

The URL is the source of truth, and only non-default values are written. Defaults are: groups on, requests **off**, archived on, exclude system on, and tz from the browser. `setFilters({ people: ["p1"] })` keeps any other URL params as they are, so pages can store their own params (e.g. `?metric=words`) next to the filters.

## Charts

```tsx
import { ChartCard } from "@/charts/ChartCard";
import { EChart } from "@/charts/EChart";
import { recordsToTable } from "@/charts/csv";
import { defaultGrid, timeSeriesZoom } from "@/charts/sampling";

const q = useApi<Timeline>("/api/overview/timeline");
const rows = q.data?.points ?? [];
<ChartCard
  title="Messages over time"
  info="Messages per bucket, stacked by your top 10 contacts."
  query={q} // loading skeleton + error card + retry
  empty={q.isSuccess && rows.length === 0}
  data={recordsToTable(rows, ["date", { key: "count", header: "Messages" }])} // CSV + "view as table"
  actions={<BucketToggle />} // optional header controls
>
  <EChart
    option={{ grid: defaultGrid({ slider: true }), dataZoom: timeSeriesZoom(), xAxis: { type: "time" }, yAxis: {}, series: [...] }}
    onEvents={{ click: (p) => open({ start: p.name, end: p.name }) }}
    height={320}
  />
</ChartCard>
```

- `ChartCard` handles the title, ⓘ tooltip, PNG export (2×, themed background, title and date range), CSV export, fullscreen, the "view as table" toggle, the skeleton and the error card. The `EChart` inside it registers itself for PNG export on its own.
- `EChart` applies the shared light/dark theme and `aria`. Line series longer than 5k points get `sampling: "lttb"` automatically (§9). It uses `notMerge` by default.
- Import ECharts only from `@/charts/registerEcharts` (the word cloud is already registered there): `import { echarts } from "@/charts/registerEcharts"`. Series type `"wordCloud"` is available.
- Heatmap and calendar ramps: `CHART_TOKENS[theme].sequential` (`@/charts/theme`) together with `useResolvedTheme()`.
- Grid of cards: `<div className="card-grid">` is one column on phones and two on xl screens.

## Colors

`personColor(person)` from `@/lib/colors` accepts `{ id, color_index }`, a numeric color index or a string id. It prefers the backend `color_index` and falls back to a hash of the id, so a person has the **same color everywhere**. Use it for every per-person series, and `OTHERS_COLOR` for "others". Index 0 is the owner by convention. The palette is colour-blind friendly (Okabe–Ito plus Tol). Don't rely on color alone: add labels or a legend.

## Drilldown (§5.1, §5.9)

```tsx
<Tile label="Sent by me" value={n} query={{ sender: ownerId }} />;
const open = useOpenMessages();
open({ thread: chatId, emoji: "😂" });
<Link to={messagesHref({ thread: id }, searchParams)}>…</Link>;
```

`MessageQuery` keys become `/search` URL params. Both spellings are accepted — the short ones (`thread, sender, type, q, mode`) and the backend `MessageQuery` names (`thread_id, sender_ids, types, text, text_mode, hour, message_ids, …`) — and are written in the canonical backend spelling; nulls/empty values are dropped and arrays comma-joined, so a backend `drilldown` can be passed as-is. The current global filters are carried over. The query's `start`/`end` override the global range. `/search` parses them with `parseBrowserParams` (round-trip tested in `src/lib/drilldown.test.ts`).

Backend drilldowns: `toMessageQuery(drilldown, extra?)` (always an object), `drilldownQuery(drilldown)` (`undefined` when there is none, for `Tile query=`), `useDrill()(drilldown, extra?)`, `useOpenContext()(messageId, threadId?)` — all in `@/lib/drilldown`.

## Components

- `Tile` / `TileGrid`: `Tile({ label, value, format: "number"|"compact"|"percent"|"duration"|"text", hint, query, onClick, loading, icon })`.
- `DataTable`: build columns with `dataTableColumnHelper<Row>()` from `@/components/dataTableColumns`. Props: `{ data, columns, getRowId?, initialSorting?, sorting?, onSortingChange?, manualSorting?, virtualize?: { height, rowHeight? }, onRowClick?, onEndReached?, caption?, empty? }`. This is TanStack Table **v9** (`useTable`, `table.FlexRender`), not v8.
- `PersonBadge({ name, id, color_index, size, showName })`
- `MessageBubble({ text, senderName, senderId, senderColorIndex, timestamp, isMe, reactions, attachments, unsent, highlight, timeZone, compact, onClick })`
- `ErrorCard({ error, onRetry })`, `Skeleton`, `ChartSkeleton`, `EmptyState({ title, description, action })`
- `Segmented({ value, options, onChange, label })` (`@/components/Segmented`), `SelectInput({ value, onChange, label, size: "sm"|"md" })` (`@/components/SelectInput`).
- Chart → CSV tables: `timeSeriesTable`, `categoryTable`, `heatmapTable` from `@/charts/tables`.
- ECharts HTML tooltip `formatter`s must wrap every user-derived string in `escapeHtml` (`@/lib/utils`). Axis/series `label` formatters render on canvas and need no escaping.
- Print: `PrintButton` (`@/features/print/PrintButton`); `?print=1` on a chat renders the report (`ChatPrintReport`). `@/styles/print.css` is imported in `main.tsx`.
- `PagePlaceholder`: delete it from your page when you implement the page.
- CSS helpers (`src/index.css`): `.page`, `.page-title`, `.card`, `.card-grid`, `.btn`, `.btn-sm`, `.btn-primary`, `.icon-btn`, `.input`, `.tooltip`. Theme colors are Tailwind tokens: `bg-bg`, `bg-surface`, `bg-surface-2`, `border-border`, `text-fg`, `text-muted`, `text-accent`, `text-danger`. Dark mode uses the `dark` class.

## Verify before handing back

```bash
npm run typecheck && npm run lint && npm run test && npm run build
npm run gen-types   # with the backend on :8000 → regenerates src/api/schema.d.ts
npm run e2e         # Playwright: build + backend on 127.0.0.1 + sample import (needs Chromium)
```

Note: `@testing-library/dom` (a peer of `@testing-library/react` and `jest-dom`) is **not installed**, so React component tests can't run yet. Pure-function tests work.
