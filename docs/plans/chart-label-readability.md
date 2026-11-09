# Plan: Fix hard-to-read chart value labels

**Status:** Implemented (label colors + `darkMode`)
**Date:** 2026-11-09
**Scope:** Frontend only (`frontend/src/charts/theme.ts`)

## Problem

The number labels next to bars (for example, the Emojis tab "top emojis" chart) are hard to read.
They show up as light gray text with a thick black outline, which looks blurry at 12px.

## Cause

1. `frontend/src/charts/theme.ts` sets `backgroundColor: "transparent"` in the ECharts theme.
2. ECharts (zrender `isDarkMode`) decides dark mode from the background's brightness. It ignores alpha,
   so `transparent` (`rgba(0,0,0,0)`) counts as black and **every chart is treated as dark mode**,
   in both the light and dark app themes.
3. Bar labels are set as `label: { show: true, position: "right" }` with no color. For labels outside
   the shape and with no color set, zrender picks colors itself (`Element.getOutsideFill` / `getOutsideStroke`):
   - text: `#ccc` (because it thinks the chart is dark)
   - outline: a 2px stroke mixed from the background. A transparent background in "dark" mode gives
     solid black (`rgba(0,0,0,1)`).
4. The theme's global `textStyle.color` doesn't apply to these labels. ECharts only uses it for
   standalone text, not labels attached to a shape.

So you get `#ccc` text with a black outline. In light mode it is probably worse: gray text with a
black outline on a white card.

## Fix

Give series labels an explicit color in the theme. When a label has its own fill,
zrender stops adding the automatic outline (`Text.js`: the default stroke only applies when
`!autoStroke || useDefaultFill`).

In `buildEchartsTheme` (`frontend/src/charts/theme.ts`):

```ts
bar:     { itemStyle: { borderRadius: 0 }, label: { color: tk.text } },
line:    { symbol: "circle", symbolSize: 4, smooth: false, label: { color: tk.text } },
scatter: { label: { color: tk.text } },
pie:     { label: { color: tk.text } },
```

Optionally, also add this at the top level of the returned theme object:

```ts
darkMode: t === "dark",
```

That stops ECharts from guessing dark mode from the transparent background, so any other automatic
colors (tooltips, emphasis labels and so on) match the app theme.

### Left out on purpose

- **`heatmap`**: its labels sit inside colored cells, and the automatic contrast coloring is correct
  there. Don't set a color for heatmap labels.
- **`graph`**: already has `label: { color: tk.text }` in the theme.
- **Charts that set their own label color** (`When.tsx` stacked bar with inside labels,
  `Timeline.tsx` mark line): the series option overrides the theme, so they don't change.

### Caveat

The `bar` theme color also applies to bars whose labels sit **inside** the bar and have no color set.
Today the only bar with inside labels (`pages/ChatDetail/tabs/When.tsx`) sets its own colors, so
nothing breaks. Any new inside-bar labels should set `color` explicitly.

## Affected charts (should all improve)

| Chart | File |
|---|---|
| Top emojis per person | `pages/ChatDetail/tabs/Emojis.tsx:140` |
| Reactions bar | `pages/ChatDetail/tabs/Reactions.tsx:178` |
| Group bar | `pages/ChatDetail/tabs/Group.tsx:247` |
| Overview ranked chats bar | `features/overview/transforms.ts:157` |
| Import timing report | `features/import/report.ts:83` |
| Pie labels (outside) | `pages/ChatDetail/tabs/Dynamics.tsx:42`, `Summary.tsx`, `Calls.tsx:162` |

## Steps

1. Edit `frontend/src/charts/theme.ts` as shown above (label colors, plus `darkMode` if wanted).
2. Run the frontend type check and tests (`make` target or `npm test` / `npm run typecheck` in `frontend/`).
3. Check in the browser, in **both** dark and light themes:
   - Emojis tab: bar numbers are crisp, with no outline.
   - Reactions, Group, Overview ranked bars, and the import report.
   - Pie charts in Dynamics, Summary and Calls: outside labels readable.
   - Heatmaps (When, Calls, Group): cell numbers still contrast with the cell color.
   - When tab stacked bar: inside labels still white / text-colored as before.
4. Export a chart as PNG (`charts/exportPng.ts`, which sets a solid background) and check that labels
   still look right in the image.

## Optional follow-up

- Bump label `fontSize` to 12 where it's smaller, if anything still looks small after the fix.
