# Plan: Fix 3 Blocking Defects in Multi-Step Prompt Presets

## Context

The multi-step presets build is **uncommitted in the working tree**. A review (`adws/adw_data/sessions/dfd81b7b/context_handoff/review.md`) found 3 blocking defects. This plan fixes exactly those — no new features, no refactors beyond what a defect requires. Do not touch `benchmarks.db`, `.claude/`, or `adws/`.

Known pre-existing issue (do NOT fix, do NOT be alarmed): bare `uv run pytest` from repo root fails collection on `adws/adw_build_test.py` / `adws/adw_plan_build_test.py` (`ModuleNotFoundError: rich`) — pre-existing on HEAD. Run tests as `uv run pytest tests/`.

---

## Defect 1 — TTFT regression (`app/main.py`, ~line 513)

**Bug**: inside the step loop of `_run_single_benchmark`:

```python
if s_ttft is not None and first_token_time is None:
    first_token_time = (time.monotonic() - start) * 1000
```

This runs **after** `await _stream_one_step(...)` returns (i.e. after step 1 fully completes), discarding the correctly measured `s_ttft` (which `_stream_one_step` computes at first-token arrival, relative to that step's start). Consequences: `time_to_first_token_ms` ≈ step-1 total time; `generation_ms = total_time_ms - ttft` collapses toward zero for single-step runs, so `tokens_per_second` falls into the wrong branch — single-step runs no longer produce the same values as before the multi-step change.

**Fix**: capture the step's start offset before each call and add the relative TTFT:

```python
for step_idx, step_prompt in enumerate(preset_steps):
    messages.append({"role": "user", "content": step_prompt})
    step_start_offset = (time.monotonic() - start) * 1000
    try:
        resp_text, s_pt, s_ct, s_ttft, s_usage, s_time = await _stream_one_step(client, messages)
    ...
    if s_ttft is not None and first_token_time is None:
        first_token_time = step_start_offset + s_ttft
```

Verify: `_stream_one_step` returns `step_first_token` measured as `(time.monotonic() - step_start) * 1000` at the first content/reasoning chunk, so `step_start_offset + s_ttft` reconstructs the absolute offset from benchmark `start`. For a single-step run, `step_start_offset` ≈ 0 and the result values match the pre-multi-step behavior exactly (same TTFT semantics, same `generation_ms`, same `tokens_per_second` branch).

No other changes in `app/main.py`.

---

## Defect 2 — Chain-run retrieval TypeError (`app/database.py`, `_row_to_benchmark_result` ~line 354)

**Bug**: `_row_to_benchmark_result` does `json.loads(d.get("steps", "[]") or "[]")` catching only `JSONDecodeError`. But `list_chain_steps` (~line 762) calls `get_result(...)`, which already decodes `steps` into a **list**, then passes that dict to `_row_to_benchmark_result` → `json.loads(list)` raises `TypeError`. Every chain run with a completed step now 500s on `db.get_chain_run` / `db.list_chain_runs` (exposed via the chain-runs GET endpoints in `app/main.py`) — a regression in existing Chain Config functionality.

**Fix** in `_row_to_benchmark_result` — only decode when the value is a string:

```python
def _row_to_benchmark_result(d: dict) -> BenchmarkResult:
    """Decode the steps JSON column before constructing BenchmarkResult.

    Accepts steps as a JSON string (raw DB row), an already-decoded list
    (e.g. from get_result), or missing/None."""
    steps = d.get("steps")
    if isinstance(steps, str):
        try:
            steps = json.loads(steps or "[]")
        except json.JSONDecodeError:
            steps = []
    d["steps"] = steps if isinstance(steps, list) else []
    return BenchmarkResult(**d)
```

Also check `get_result` (which decodes steps itself): leave its behavior as-is — the isinstance guard makes `_row_to_benchmark_result` idempotent regardless of which path produced the dict. Do not change `list_chain_steps` or `save_result`.

---

## Defect 3 — Steps editor loses typed text (`static/app.js`, ~lines 79–97)

**Bug A**: `renderPresetSteps()` rebuilds textareas from `presetSteps`, but edits never sync back into `presetSteps` (no input listener). Typing step 1 then clicking *+ Add step* / ↑ / ↓ / ✕ re-renders from stale state and destroys the typed text.

**Bug B**: `savePreset` maps state→textarea via `presetSteps.indexOf(s)`, which collapses duplicate step texts to the first matching textarea.

**Fix**:

1. In `renderPresetSteps()`, add an `oninput` handler to the textarea that syncs state by index:

   ```js
   <textarea ... oninput="presetSteps[${i}]=this.value">${esc(s)}</textarea>
   ```

   (Keep everything else in the row markup unchanged. `presetSteps` is module-level so inline handler access works, consistent with the existing inline `onclick` style.)

2. In `savePreset()`, read the textareas directly by index — never via `indexOf`:

   ```js
   async function savePreset(){
     const id=$('presetId').value;
     const steps=[...document.querySelectorAll('.preset-step-input')].map(t=>t.value.trim()).filter(Boolean);
     if(!steps.length){alert('At least one step is required');return}
     const body={key:$('presetKey').value,name:$('presetName').value,prompt:steps[0],description:$('presetDesc').value,steps};
     ... // unchanged POST/PUT logic
   }
   ```

With oninput syncing, `addPresetStep`/`removePresetStep`/`movePresetStep` re-renders operate on current state and no text is lost. No `index.html` changes needed.

---

## Regression tests (defects 1 and 2)

Add to `tests/test_multi_step_presets.py` (reuse its existing fixtures/handler pattern; do not import `_LLMHandler` from `test_benchmark_runner` — class-level mutable state leaks across files).

### Defect 1 — TTFT measured at first token

Add a new handler class, e.g. `_SlowTTFTHandler(BaseHTTPRequestHandler)`, that streams: sleep ~0.3s → first content chunk (`"Hello"`) → sleep ~0.3s → second chunk (`" world"`) + usage chunk (e.g. `{"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}`) → `[DONE]`. Fixture spins it up like the existing `llm_server` fixture.

Test `test_ttft_measured_at_first_token`:
- POST `/api/run` with endpoint + single-step preset against the slow server.
- Assert `body["time_to_first_token_ms"]` is in a tolerant window around the first delay (e.g. `200 <= ttft <= 550`) — critically `ttft < body["total_time_ms"] - 150` (proves TTFT is not ≈ total time).
- Assert `tokens_per_second` reflects the generation window, not the total: with ttft ≈ 300ms and total ≈ 600ms, `tps = completion_tokens / generation_s` should be roughly double `completion_tokens / total_s`. A robust, non-flaky form: `body["tokens_per_second"] > 1.3 * (body["completion_tokens"] / (body["total_time_ms"] / 1000))`. Keep bounds loose to avoid CI flake.
- Assert usage-based counts unchanged (`prompt_tokens == 5`, `completion_tokens == 2`).

### Defect 2 — chain-run retrieval with steps

Test `test_chain_run_retrieval_with_steps` (no HTTP server needed; use `_db_path`/`client` fixtures):
- `db.save_result(...)` a `BenchmarkResult` with `steps=[{"prompt": "p1", "response": "r1", "prompt_tokens": 5, "completion_tokens": 2, "total_time_ms": 100.0}]` (must succeed: `save_result` json-dumps steps).
- Create a `ChainRunResult` + `ChainStepResult` whose `benchmark_result` is that result; persist via `db.save_chain_run` / `db.save_chain_step`.
- Assert `db.get_chain_run(cr.id)` does not raise and `cr.step_results[0].benchmark_result.steps == [...]` (a list, round-tripped).
- Assert `db.list_chain_runs()` also works.
- Assert the HTTP surface: `GET /api/chains/{id}` (and `GET /api/chains`) return 200 with the steps list intact.

Optional cheap unit test: call `db._row_to_benchmark_result` directly with `steps` as a JSON string, as a list, and as `None` — all yield a `BenchmarkResult` with a list `steps`.

---

## Verification

1. `uv run pytest tests/` — full suite green (36 existing + new regression tests).
2. Sanity-check defect 1 by eye: in `app/main.py` the `first_token_time` assignment now uses `step_start_offset + s_ttft` and sits where the old buggy line was.

## Out of scope (explicit)

- New features, Chain Configs model-chaining changes.
- The `benchmarks.db` git-tracking/permissions question (handled separately — do not touch `benchmarks.db`, `.claude/`, `adws/`).
- The pre-existing `rich` collection failure under bare `uv run pytest`.
