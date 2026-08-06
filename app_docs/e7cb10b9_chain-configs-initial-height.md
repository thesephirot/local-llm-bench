# Increase Initial Chain Configs Sidebar Height by 40%

## What changed

The "Chain Configs" section in the sidebar now starts at **196 px** on page load instead of 140 px — a 40 % increase. The drag handle (`#sidebarDivider`) continues to resize the section exactly as before; only the initial value changed.

## Files

| File | Role |
|---|---|
| `static/index.html` (line ~210) | **The change.** In the `DOMContentLoaded` handler, `chainSec.style.height` was set from `'140px'` to `'196px'`. One line. |
| `specs/e7cb10b9_chain-configs-initial-height.md` | Spec / plan that describes the goal, the single-line edit, and verification steps. |
| `.claude/skills/sssf/templates/sssf.config.yaml` | Agent model-name tweaks (dropping `:thinking` suffix, swapping scout's model). Not related to the sidebar height change — included in the same commit envelope. |

## How to verify

1. Load the app in a browser.
2. Open DevTools → Elements → pick `#chainConfigsSection`. Its computed `height` should be **196 px**.
   - Or run: `document.getElementById('chainConfigsSection').style.height === '196px'` in the console.
3. Click and drag `#sidebarDivider` up / down — the section resizes normally; the drag logic is untouched.
4. Reload the page — the section resets to 196 px (size persistence across reloads is out of scope).

## Scope notes

- Only the initial height on load changed. The resize-on-drag code was not touched.
- "Chain LLMs" in the main config bar, the Presets section sizing, and any persist/storing of the sidebar size across page reloads were explicitly left alone.
