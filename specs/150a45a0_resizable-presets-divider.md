# Plan: Resize PRESETS section & add resizable divider between CHAIN CONFIGS and PRESETS

## Files to modify

Only one file needs changes:

- **`static/index.html`** — sidebar layout (HTML structure + inline JS for resize)
- **`static/app.js`** — no changes needed; the resize handle is pure DOM + CSS.

---

## 1. Sidebar HTML restructuring

The current sidebar has three independent flex children (Endpoints, Chain Configs, Presets). We restructure so that **Chain Configs + Presets live inside a shared flex-column container** with a draggable divider between them.

### Current structure (simplified)

```html
<aside class="w-72 ...">
  <!-- Endpoints section -->
  <div class="p-4 border-b ...">...</div>
  <!-- Chain Configs section -->
  <div class="p-4 border-b ...">...</div>
  <!-- Presets section -->
  <div class="p-4 border-b ...">...</div>
</aside>
```

### New structure

```html
<aside class="w-72 ...">
  <!-- Endpoints section (unchanged) -->
  <div id="endpointSection" class="p-4 border-b ...">...</div>

  <!-- Resizable container for Chain Configs + Presets -->
  <div id="sidebarResizable" class="flex flex-col h-full">
    <!-- Chain Configs section — gets a resize handle at its bottom -->
    <div id="chainConfigsSection" class="p-4 border-b ... overflow-hidden">
      ...
    </div>

    <!-- Draggable divider line -->
    <div id="sidebarDivider"
         class="h-2 cursor-ns-resize flex items-center justify-center shrink-0 select-none hover:bg-cyan-500/30 transition-colors active:bg-cyan-500/50">
      <div class="w-8 h-0.5 rounded-full bg-gray-600 group-hover:bg-gray-400"></div>
    </div>

    <!-- Presets section — all 5 presets visible, no max-height cap -->
    <div id="presetsSection" class="p-4 overflow-hidden">
      ...
    </div>
  </div>
</aside>
```

### Key changes in the HTML

1. **Presets `max-h-40` removed** — replace with just `overflow-y-auto` so it grows to fit content. If presets grow too tall they can scroll, but all 5 seed presets will always be visible at default size since there's no artificial cap.

2. **Chain Configs `max-h-36` replaced** with a class that sets an initial height (e.g., `h-36`) — the resize handle will adjust this dynamically.

3. Wrap both sections in a `<div id="sidebarResizable" class="flex flex-col h-full">` so they share the remaining sidebar space.

4. Insert a divider element between them with:
   - `cursor-ns-resize` for the grab cursor
   - `h-2` (8px tall) for easy grabbing
   - A visual indicator (a small horizontal line)
   - Hover/active color feedback

---

## 2. Inline resize script (add to `<script>` in `<head>`)

Add a small block of JS right after the Tailwind config script in `<head>`:

```html
<script>
(function() {
  const container = document.getElementById('sidebarResizable');
  const divider   = document.getElementById('sidebarDivider');
  const chainSec  = document.getElementById('chainConfigsSection');
  const presetSec = document.getElementById('presetsSection');
  let dragging = false;

  function startDrag(e) { e.preventDefault(); dragging = true; };
  function endDrag()    { dragging = false; };

  divider.addEventListener('mousedown', startDrag);
  window.addEventListener('mouseup', endDrag);
  window.addEventListener('mousemove', function(e) {
    if (!dragging) return;
    const rect = container.getBoundingClientRect();
    const chainH = Math.max(60, Math.min(e.clientY - rect.top - 8, rect.height - 120));
    chainSec.style.flex = 'none';
    chainSec.style.height = chainH + 'px';
    presetSec.style.flex = '1 1 auto';
    presetSec.style.overflowY = 'auto';
  });

  // Ensure presets section shows all items by default
  document.addEventListener('DOMContentLoaded', function() {
    if (chainSec && presetSec) {
      chainSec.style.flex = 'none';
      chainSec.style.height = '140px'; // initial height ≈ current max-h-36
      presetSec.style.flex = '1 1 auto';
    }
  });
})();
</script>
```

### Behavior notes

- **Initial state**: Chain Configs gets ~140px (matching the old `max-h-36`), Presets fills remaining space.
- **Dragging down on divider**: Chain Configs grows, Presets shrinks.
- **Dragging up on divider**: Chain Configs shrinks (min 60px), Presets grows.
- **Presets section**: No artificial height cap — all 5 presets visible by default. If the user drags the divider to make it very small, it scrolls internally (`overflow-y-auto`).

---

## 3. CSS tweaks (inline `<style>`)

Add one class for the resize cursor feel:

```css
.sidebar-divider:hover { background: rgba(6,182,212,0.15); }
.sidebar-divider:active { background: rgba(6,182,212,0.25); }
```

(Or use Tailwind utility classes directly on the divider element — no new CSS needed.)

---

## Verification

1. Open the app in a browser (`start.sh` then navigate to `http://localhost:8000`).
2. **All 5 presets visible**: The Presets section should show all 5 seed presets without scrolling at default size.
3. **Divider is draggable**: Hover over the line between Chain Configs and Presets — cursor changes to `ns-resize`. Click and drag up/down — both sections resize smoothly.
4. **Presets scroll when squeezed**: If you drag the divider all the way down, the Presets section scrolls internally rather than clipping items.
5. **Chain Configs min-height respected**: You can't shrink Chain Configs below ~60px (a minimal usable height).
