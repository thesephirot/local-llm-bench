# Fix 3 Blocking Defects in Multi-Step Prompt Presets

## What Changed and Why It Matters

The multi-step prompt presets feature was built into the working tree but contained three blocking defects found during review. This work fixes all three so that:

1. **Single-step runs produce identical metrics** — TTFT is measured at first-token arrival again, `generation_ms` is correct, and `tokens_per_second` uses the right denominator. Before this fix, a single-step run's TTFT was ≈ total_time because the post-refactoring code read `time.monotonic() - start` *after* `_stream_one_step` returned, discarding the step-relative TTFT measured at first content chunk.

2. **Chain-run retrieval no longer crashes** — `GET /api/chains/{id}` and `db.get_chain_run()` return 200 with intact step data. Before this fix, `_row_to_benchmark_result` called `json.loads()` unconditionally on the `steps` field; but `list_chain_steps` already decoded steps into a Python list via `get_result()`, so `json.loads(list)` raised `TypeError`.

3. **The preset steps editor never loses typed text** — typing in a step's textarea syncs into the state array on every keystroke (`oninput`), and `savePreset` reads textareas by DOM index rather than `indexOf(step_text)`, so duplicate step texts no longer collapse to a single textarea.

## Files Changed

### `app/main.py` — TTFT fix + multi-step execution core
- **Defect 1 fix** (~line 513): Added `step_start_offset = (time.monotonic() - start) * 1000` before each `_stream_one_step` call; TTFT is now `step_start_offset + s_ttft`, reconstructing the absolute benchmark-start offset from the step-relative first-token time.
- **Multi-step execution**: `_run_single_benchmark` now loops over `preset_steps`, threading messages sequentially across API calls. Each step's response becomes the assistant message for the next step.
- `_stream_one_step`: extracted SSE streaming logic into a reusable async function that returns `(response_text, prompt_tokens, completion_tokens, first_token_ms, usage_seen, step_time_ms)`.
- Aggregated metrics: `completion_tokens` sums across steps; `prompt_tokens` uses last-step usage (full context) or falls back to char/4 estimation.
- `preset_steps` parameter replaces the old single `preset_prompt`; backward-compatible — if only `preset_prompt` is passed, it wraps into `[preset_prompt]`.

### `app/database.py` — steps persistence + decode guard
- **Defect 2 fix** (`_row_to_benchmark_result`, ~line 354): Added `isinstance(steps, str)` guard so `json.loads` only runs on raw JSON strings from a fresh DB row. Already-decoded lists (from `get_result`) pass through untouched — the function is now idempotent regardless of call path.
- New `_row_to_preset` helper decodes preset steps JSON and falls back to `[prompt]` when steps are empty (backward compat for legacy presets).
- DB schema migrations: added `steps TEXT NOT NULL DEFAULT '[]'` column to both `presets` and `results` tables.
- `save_result` json-dumps `r.steps`; `list_results`, `list_results_filter`, `get_result`, and `list_chain_steps` all route through the new decode helpers.

### `app/models.py` — data model updates
- `PromptPreset`: added `steps: list[str]` field (default empty list).
- `BenchmarkResult`: added `steps: list[dict]` field for per-step detail (`{prompt, response, prompt_tokens, completion_tokens, total_time_ms}`).

### `static/app.js` — steps editor state sync
- **Defect 3 fix**: `renderPresetSteps()` now adds `oninput="presetSteps[${i}]=this.value"` to each textarea, syncing keystrokes back into the module-level `presetSteps` array.
- `savePreset` reads textareas directly: `[...document.querySelectorAll('.preset-step-input')].map(t=>t.value.trim()).filter(Boolean)`, indexed by DOM position — duplicate step texts no longer collapse.
- New helper functions: `addPresetStep()`, `removePresetStep(i)`, `movePresetStep(i, delta)` with full state management.
- Preset list buttons show a step-count badge when a preset has >1 step.
- Detail modal (`showDetail`) now renders per-step prompt/response blocks in `#detailSteps` when `r.steps.length > 1`.

### `static/index.html` — UI scaffolding
- Preset modal: replaced single `#presetPrompt` textarea with `#presetStepsList` container + "Add step" button.
- Detail modal: added `#detailSteps` div (hidden by default, shown when result has multiple steps).

### `tests/test_multi_step_presets.py` — regression tests
- **Defect 1**: `test_ttft_measured_at_first_token` — uses a fake LLM that sleeps ~150ms before first content chunk. Asserts TTFT < total_time - 100 and tokens_per_second > 1.3 × (completion_tokens / total_s), proving the generation window is used.
- **Defect 2**: `test_chain_run_retrieval_with_steps` — saves a BenchmarkResult with steps, creates a ChainRunResult referencing it, asserts `db.get_chain_run()` and `GET /api/chains/{id}` return 200 with intact step data. Also `test_row_to_benchmark_result_idempotent` unit-tests the decode helper with str/list/None inputs.
- Additional coverage: multi-step execution context (message threading), persistence round-trip, backward compat with legacy single-prompt presets, API validation (create/update/reject-empty), mid-chain step failure handling.

## How to Verify

1. **TTFT regression test**: `uv run pytest tests/test_multi_step_presets.py::test_ttft_measured_at_first_token -v`
   - Uses a fake LLM server that delays 150ms before first token. Verifies TTFT ≈ 150ms (not ≈ total_time) and tokens_per_second reflects the generation window.

2. **Chain-run retrieval test**: `uv run pytest tests/test_multi_step_presets.py::test_chain_run_retrieval_with_steps -v`
   - Saves a result with steps, creates a chain run referencing it, verifies no TypeError on retrieval via both DB and HTTP layers.

3. **Full test suite**: `uv run pytest tests/ -v`
   - All existing tests plus 10 new regression tests (multi-step execution, persistence, backward compat, validation, failure handling, TTFT, chain-run retrieval, row decode idempotency).

4. **Manual — multi-step preset creation**:
   - Open the app → Presets modal → "Prompt Steps" shows a numbered list with + Add step button.
   - Type text into each step's textarea, add/remove/move steps, save. The step count badge appears on the preset button.
   - Run a benchmark with the multi-step preset → result detail modal shows per-step prompt/response blocks.

5. **Manual — backward compat**: Old presets with no `steps` column still work; `list_presets()` reconstructs `steps = [prompt]` automatically.
