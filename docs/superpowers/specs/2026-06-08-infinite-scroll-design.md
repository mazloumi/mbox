# Infinite Scroll (auto-load) — Design

**Date:** 2026-06-08
**Status:** Approved

## Goal

Replace the manual **"Load more…"** click with automatic loading of the next page when the user
scrolls near the bottom of the message/file list.

## Decision

Use an `IntersectionObserver` on a bottom **sentinel** element, rooted at the scroll container
(`#list`), with a `rootMargin` prefetch so the next page loads slightly before the user hits the
bottom. Frontend-only; no API/backend change. Keep a click fallback for the (rare) browser without
`IntersectionObserver`.

## Design (`static/app.js`)

- A single persistent sentinel `<li id="load-more" class="load-more">` (id kept so arrow-key nav keeps
  excluding it). Text "Loading…" when auto, "Load more…" for the click fallback.
- One module-level `IntersectionObserver` (created if supported), `root: #list`,
  `rootMargin: "400px"`, that calls `loadNextPage()` when the sentinel intersects.
- State guards: `loadingMore` (a fetch is in flight) and `noMorePages` (last page was short).
- `renderLoadMore(lastCount)`:
  - `lastCount === PAGE_SIZE` → append the sentinel to the list bottom and **re-observe** it
    (`unobserve` then `observe`, forcing a fresh intersection check so a short list that doesn't fill
    the viewport keeps loading until a short page); no observer → `onclick = loadNextPage`.
  - else → `noMorePages = true`, unobserve + remove the sentinel.
- `loadNextPage()`: return early if `loadingMore || noMorePages`; set `loadingMore`, bump
  `currentPage`, fetch, `appendRows`; on error set `noMorePages` + remove the sentinel; `finally`
  clears `loadingMore`. The re-observe in `renderLoadMore` (called by `appendRows`) drives continued
  auto-loading.
- `reload()`: reset `loadingMore = false` and `noMorePages = false` before clearing the list (the
  `innerHTML = ""` detaches the sentinel; it's re-appended by the next `appendRows`).

Termination: the API returns `< PAGE_SIZE` rows on the last page → `noMorePages` → sentinel removed →
observer idle. Re-observing is bounded by the data (stops at the first short page), so no infinite
loop. `loadingMore` prevents overlapping fetches.

## `static/style.css`
`.load-more { text-align: center; color: #888; cursor: default; }` (the click fallback still works).

## Testing
- **Browser:** with a label/category having > 50 items, scrolling the list to the bottom auto-loads
  the next page (no click); it keeps loading on continued scroll and stops at the end; switching
  label/category/mode resets paging; arrow-key nav still skips the sentinel; a forced no-IO path
  still loads on click.

## Out of scope
- Virtualized/windowed lists (rows are not removed as you scroll — fine at these page sizes).
- A scroll-to-top control.
