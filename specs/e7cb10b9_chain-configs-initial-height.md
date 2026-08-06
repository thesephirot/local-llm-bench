# Plan: Increase initial Chain Configs sidebar height by 40%

## Goal
On page load, the sidebar "Chain Configs" section starts at 196px instead of 140px (a 40% increase). The `#sidebarDivider` drag handle must keep resizing the section exactly as before.

## File to change
`static/index.html` — the IIFE init script at the bottom of the file (the `sidebarResizable` script, around lines 184–215).

## Change
In the `DOMContentLoaded` handler (currently lines 208–214):

```js
document.addEventListener('DOMContentLoaded',function(){
  if(chainSec&&presetSec){
    chainSec.style.flex='none';
    chainSec.style.height='140px';   // <- change this line
    presetSec.style.flex='1 1 auto';
  }
});
```

Change the single line:
```js
chainSec.style.height='140px';
```
to:
```js
chainSec.style.height='196px';
```

(140 × 1.4 = 196.)

No other edits: do not touch the drag logic (`onMove`, divider handlers), the markup, the Presets section sizing, or the main-bar "Chain LLMs" list. No persistence of size across reloads.

## Verification
1. Serve the app as usual (check README/scripts; typically the static file is served by the project's server) and load the page.
2. Confirm `#chainConfigsSection` computed height is 196px on initial load (DevTools, or `document.getElementById('chainConfigsSection').style.height === '196px'`).
3. Drag `#sidebarDivider` up/down and confirm the Chain Configs section resizes exactly as before (drag logic untouched — it sets `chainSec.style.height` from pointer movement and this change only affects the initial value).
4. Reload and confirm the section resets to 196px (persistence is explicitly out of scope).
