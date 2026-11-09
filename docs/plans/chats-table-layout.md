# Plan: Chats table: fix page scroll growth and fill the screen

**Status:** Implemented (steps 1–4; step 5 not done)
**Date:** 2026-11-09
**Scope:** Frontend only: `components/DataTable.tsx`, `app/Layout.tsx`, `pages/Chats/index.tsx`

## Problems

1. **The page scrollbar shrinks while you scroll the table.** Scrolling inside the chats table
   makes the whole page taller (and wider). The outer scrollbar keeps shrinking, and a horizontal
   scrollbar shows up at the bottom of the page.
2. **Wasted space.** The table is a fixed 640px box inside a page capped at 1536px
   (`max-w-screen-2xl`). On a 1920px screen there is empty space below and on both sides, while the
   Activity and Trend columns are cut off.

## Cause of problem 1

- The Trend column renders `<span className="sr-only">` in every row. Tailwind's `sr-only` is
  `position: absolute`.
- An absolute element is positioned against its nearest ancestor with `position` set. The
  `DataTable` scroll box (`overflow-auto`) has no `position`, so the spans skip past it and attach
  to the page scroller in `app/Layout.tsx`, which is `relative`.
- Because they attach outside the table box, the box doesn't clip them. They sit where they would
  be in the unscrolled table: row N is about N × 40px down, and the Trend column is far to the right.
  That stretches the page scroller.
- The table only renders the rows near the scroll position. As you scroll, deeper rows render and
  their spans push the page further. That explains the growing height, and the off-screen Trend
  column explains the horizontal scrollbar.

Checked in headless Chromium: an outer scroller 600px tall, with an inner `overflow:auto` box
holding one absolute span at y=2500, x=1900.

| Inner box | Outer scrollHeight | Outer scrollWidth |
|---|---|---|
| no `position` | 2501 | 1901 |
| `position: relative` | 600 (no overflow) | 800 |

## Design

### Step 1: Fix the bug (needed no matter what else we do)

`components/DataTable.tsx`: add `relative` to the scroll box:

```tsx
className={cn("relative overflow-auto rounded-lg border border-border", className)}
```

This keeps the hidden text inside the table box. It covers every `DataTable` user (People,
Person, Summary, Calls, Reactions, Media, Words, Import report, Overview).

### Step 2: Let a virtualized table fill its container

`components/DataTable.tsx`: let `virtualize.height` also take `"fill"`:

```ts
virtualize?: { height: number | "fill"; rowHeight?: number };
```

- Number: same as today (inline `style={{ height }}`).
- `"fill"`: no inline height. Add `min-h-0 flex-1` to the scroll box so it takes the space its
  flex-column parent gives it. TanStack Virtual already watches the scroll box's size
  (ResizeObserver), so the visible row range updates when the window is resized.

The other virtualized users (`Summary.tsx` with 520px, `Person/index.tsx` with 420px) don't change.

### Step 3: Let pages stretch to the full height below the top bar

`app/Layout.tsx`: make `<main>` a flex column so a page can fill it:

```tsx
<main id="main" className="flex min-h-0 min-w-0 flex-1 flex-col">
```

`min-h-0` is required. Without it, `<main>` gets the browser's default minimum height (its
content's height), grows to the full table height (about 5,600px with 140 chats), and the table
never gets a fixed height.

Other pages are unaffected: `.page` doesn't use `flex-1`, so it still sizes to its content. It
overflows `<main>` into the outer scroller, which scrolls as before. `mx-auto` still centers it horizontally inside a column flex
container.

### Step 4: Full-height, full-width Chats page

`pages/Chats/index.tsx`:

- Page wrapper: `className="page min-h-0 max-w-none flex-1"`
  - `flex-1 min-h-0`: the page is exactly the height left below the top bar, so the page itself
    doesn't scroll.
  - `max-w-none`: removes the 1536px width cap on this page only. This overrides the class the same
    way `page max-w-4xl` does on the Import page. That gains about 150px on a 1920px screen.
- Table: `virtualize={{ height: "fill", rowHeight: 40 }}`.
- Give the table a minimum height, such as `className="min-h-[24rem]"`, so on very short windows
  the table stays usable and the page scrolls instead.
- Keep the "Showing X of Y" line under the table. It stays a fixed-height row in the column.

Result: one vertical scrollbar (the table's), the horizontal scrollbar pinned to the bottom of the
window, and the sticky header and row virtualization working as before.

### Step 5 (optional): Fit more columns

- Cell padding `px-3` → `px-2` in `DataTable` header and body cells. This affects all tables, so
  it could instead be a `dense` prop that only Chats uses.
- The Title column is capped at `max-w-[16rem]`. Leave it, or drop to `14rem` if columns still
  don't fit.

## Rejected alternative: whole page scrolls, no box

Let the table grow to full height and scroll the page (window virtualizer). It's rejected because
the table is wider than the screen, so it still needs its own horizontal scroll box:

- A sticky `<thead>` inside an `overflow-x` box sticks to that box, not the page, so the header
  would stop sticking.
- The horizontal scrollbar would sit at the very bottom of the full list, far off screen.

## Verification

1. `npm run typecheck`, `npm run lint`, `npm test`, and `npm run e2e` (the e2e test clicks the first
   chats row).
2. Chats page at about 1920×1080:
   - Scroll the table top to bottom: the page scrollbar does **not** appear or change, and there's
     no horizontal page scrollbar.
   - The table fills the height down to the "Showing…" line, and the horizontal scrollbar is visible
     at the bottom.
   - Activity and Trend columns visible (or nearly) without horizontal scrolling.
   - Resize the window taller and shorter: the table follows and rows fill in.
   - Infinite loading still fires near the end. With fewer rows than the window height, the table
     ends early (OK).
   - Short window (under about 600px tall): the page scrolls and the table keeps its minimum height.
   - Mobile width: sidebar hidden, table usable.
3. Other pages look and scroll the same: Overview, People, Person (virtualized table), Chat
   detail tabs (Summary with a virtualized table), Search, Import, Import report.
4. Light and dark themes.

## Possible follow-up

- The Search page (`MessageList`) could use the same full-height layout.
