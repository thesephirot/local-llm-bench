# Resizable Presets Section & Divider Between Chain Configs and Presets

## What Changed

The sidebar layout was restructured so that **Chain Configs** and **Presets** share a common flex container with a **draggable divider** between them. The Presets section previously had a `max-h-40` cap (80px) that hid presets beyond the first few — that cap is gone, so all 5 seed presets are visible at default size.

## Files Changed

### `static/index.html`

**Sidebar HTML restructuring:**

- Three independent flex children (Endpoints, Chain Configs, Presets) → Endpoints stays standalone; Chain Configs + Presets now live inside `<div id="sidebarResizable" class="flex flex-col min-h-0">`.
- **Presets section**: Removed `max-h-40`, replaced with `overflow-hidden` on the wrapper and `overflow-y-auto` on the inner `#presetList`. No artificial height cap means all 5 presets show by default.
- **Chain Configs section**: Replaced `max-h-36` with dynamic sizing via inline JS. Initial height set to `140px` (roughly matching the old `max-h-36`).
- **Divider element** (`#sidebarDivider`): 8px-tall grab bar between the two sections with `cursor-ns-resize`, a subtle horizontal line indicator, and cyan hover/active color feedback.

**Inline resize script** (new `<script>` block before `app.js`):

```js
// Key behavior:
// - mousedown on divider → start dragging
// - mousemove on window → adjust chainSec.height (min 80px, max containerHeight-160)
// - mouseup on window → stop dragging
// - DOMContentLoaded → set initial state: chainSec 140px fixed height, presetSec flex-grows to fill remainder
```

### `specs/150a45a0_resizable-presets-divider.md`

A plan/spec document describing the same change in detail (HTML structure, JS behavior, CSS notes, verification steps). It was added as a spec artifact alongside the implementation.

## How to Verify

1. Open the app (`start.sh`, then `http://localhost:8000`).
2. **All 5 presets visible** — the Presets section in the sidebar shows all seed presets without scrolling at default size.
3. **Draggable divider** — hover the line between Chain Configs and Presets; cursor changes to vertical resize arrows. Click and drag up/down to resize both sections.
4. **Presets scroll when squeezed** — dragging the divider far down makes the Presets section internally scrollable rather than clipping items.
5. **Chain Configs min-height respected** — can't shrink Chain Configs below 80px.
