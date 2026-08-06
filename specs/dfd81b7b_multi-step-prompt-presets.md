# Plan: Multi-Step Prompt Presets

## Goal

Extend prompt presets from a single `prompt` string to an **ordered list of prompt steps**. Running a preset executes the steps sequentially as one continuous multi-turn conversation (each step's prompt and prior responses stay in the message context sent to the model). Every step's response is recorded and visible in results/history. Single-prompt presets keep working unchanged (treated as a one-step preset).

Out of scope: Chain Configs (model chaining) changes, branching/conditional logic, per-step temperature or max_tokens overrides.

## Key design decisions

1. **Storage**: keep the existing `prompt` column on `presets` (always kept in sync with `steps[0]`, so every legacy consumer keeps working) and add a new `steps` TEXT column holding a JSON array of prompt strings. This mirrors the existing `chain_configs.model`/`models` dual-column precedent already in the codebase.
2. **Execution**: generalize `_run_single_benchmark` in `app/main.py` to accept an ordered list of prompt steps and loop over them, threading an OpenAI-style `messages` array across iterations (`user` → `assistant` → `user` → …).
3. **Recording**: one `BenchmarkResult` per preset run (not one per step), with a new `steps` field — a list of `{prompt, response, completion_tokens, prompt_tokens, total_time_ms}` dicts persisted as JSON in a new `results.steps` column. Aggregate metrics stay on the top-level result so the history table, summary, trends, and compare views need no changes: `response` = last step's response, `prompt` = first step's prompt, `completion_tokens`/`total_time_ms` = sums across steps, `tokens_per_second` computed from the summed values.
4. **Backward compat**: any preset with empty/missing `steps` is treated as `[prompt]`. The `presets_as_dict()` response gains a `steps` key but keeps `prompt`. Existing `/api/run` and chain execution paths call the same generalized core; a one-step run produces byte-identical request payloads to today.

## Changes by file

### `app/models.py`

- `PromptPreset`: add `steps: list[str] = field(default_factory=list)` after `prompt`.
- `BenchmarkResult`: add `steps: list[dict] = field(default_factory=list)` — per-step `{prompt, response, prompt_tokens, completion_tokens, total_time_ms}` records. Empty for legacy rows.

### `app/database.py`

- `init_db()`: add `("presets", "steps", "TEXT NOT NULL DEFAULT '[]'")` and `("results", "steps", "TEXT NOT NULL DEFAULT '[]'")` to the existing `ALTER TABLE … ADD COLUMN` migration loop (the pattern already used for `results.success`, etc.). No backfill SQL needed — decode-time fallback covers legacy rows.
- Add a `_row_to_preset(row)` helper (mirroring `_row_to_chain_config`): `json.loads` the `steps` column with `JSONDecodeError` fallback to `[]`; if empty, fall back to `[row["prompt"]]`. Use it in `list_presets()`.
- `save_preset()`: write `steps` as `json.dumps(p.steps)`; keep writing `prompt` as `p.prompt` (callers ensure `prompt == steps[0]`).
- `presets_as_dict()`: include `"steps": p.steps` in each entry.
- `save_result()`: add `steps` to the INSERT (as `json.dumps(r.steps)`).
- Result readers (`list_results`, `list_results_filter`, `get_result`, and anywhere `BenchmarkResult(**dict(r))` is constructed): `get_result` returns a raw dict — decode `steps` JSON there (`d["steps"] = json.loads(d["steps"] or "[]")`). For the dataclass constructors, pop/decode `steps` before `BenchmarkResult(**d)` (e.g. a small `_row_to_benchmark_result(dict)` helper) so a JSON string never lands in the dataclass field. Check every `BenchmarkResult(**` call site: `list_results`, `list_results_filter`, `list_chain_steps`.

### `app/main.py`

- Schemas: add `steps: list[str] = []` to `PresetCreate` and `PresetUpdate`. Keep `prompt` required-but-now-derivable: change to `prompt: str = ""` and normalize in the routes.
- `create_preset` / `update_preset` routes: normalize — if `steps` empty and `prompt` non-empty, `steps = [prompt]`; strip/ignore empty step strings; set `prompt = steps[0] if steps else data.prompt`; reject (400) if the final step list is empty. Construct `PromptPreset(..., steps=steps)`.
- `_run_single_benchmark`: replace `preset_prompt: str` with `preset_steps: list[str]` (keep a thin compat: callers pass `preset.get("steps") or [preset.get("prompt", …)]`). Rework the body:
  - Hoist header building as-is.
  - Extract the current streaming loop into an inner helper, e.g. `_stream_one_step(client, messages) -> tuple[response_text, prompt_tokens, completion_tokens, first_token_ms, usage_seen, total_time_ms]`, preserving all current behavior (SSE buffering, reasoning_content TTFT handling, usage chunk capture, error classification via the existing `_fail` path).
  - Outer loop over steps: `messages.append({"role":"user","content":step})` → run one streaming request → append `{"role":"assistant","content":response_text}` → record a per-step dict into `result.steps`. On failure of any step, return `_fail(...)` with the steps completed so far already attached (persisted via `save_result`, which `_fail` already calls).
  - After the loop: aggregate — `result.response` = last step's response (keep the reasoning-fallback behavior per step), `result.prompt` = first step's prompt, `result.completion_tokens` = sum, `result.prompt_tokens` = last step's measured `prompt_tokens` if usage was seen (it reflects the full conversation) else the sum of estimates, `result.total_time_ms` = sum of step times, `result.time_to_first_token_ms` = first step's TTFT, `tokens_per_second` from the same formula as today applied to the aggregates, `tokens_estimated` if any step estimated.
  - A single-step run must produce exactly the same request payload and result values as today.
- Call sites: `/api/run` route and `_execute_chain` — pass `preset_steps=preset.get("steps") or [preset.get("prompt", "Hello, how are you today?")]` (chain) / `preset.get("steps") or [preset["prompt"]]` (run). `_step_to_dict` needs no change.
- `/api/run` response: FastAPI serializes the dataclass via `asdict`-equivalent (it returns the dataclass; verify `steps` appears in the JSON — the existing route returns the dataclass directly, which FastAPI encodes including new fields).

### `static/index.html`

Preset modal (`#presetModal`, ~line 126–144):
- Replace the single `<textarea id="presetPrompt">` block with a steps editor: a container `<div id="presetStepsList">` plus an `+ Add step` button (`onclick="addPresetStep()"`). Each step row rendered by JS: a step-number label, a textarea (class `preset-step-input`), and small `↑` `↓` `✕` buttons calling `movePresetStep(i, dir)` / `removePresetStep(i)`.
- Keep `presetId`, `presetKey`, `presetName`, `presetDesc` unchanged.

Result detail modal: `showDetail` populates `detailPrompt`/`detailResponse`; add a `detailSteps` container div (hidden when empty) so multi-step runs can render per-step prompt/response.

### `static/app.js`

- New modal state + functions:
  - `let presetSteps=[]` module-level.
  - `renderPresetSteps()` — rebuild `#presetStepsList` from `presetSteps` (preserve focus-friendly behavior: full re-render is fine, matching the app's existing style).
  - `addPresetStep(text='')`, `removePresetStep(i)`, `movePresetStep(i,dir)` — mutate `presetSteps` and re-render. Disable `✕` when only one step remains; disable `↑` on first / `↓` on last.
  - `showPresetModal()`: `presetSteps=['']` then render.
  - `editPreset(id,key)`: `presetSteps = (p.steps && p.steps.length ? [...p.steps] : [p.prompt||''])` then render.
  - `savePreset()`: collect `steps = presetSteps.map(s=>s.trim()).filter(Boolean)`; `alert` and abort if empty; body = `{key, name, prompt: steps[0], steps, description}`.
- `loadPresets()`: in the preset list button, append a step-count badge (e.g. `· 3 steps`) when `p.steps?.length > 1`. No other changes — `selPreset` and `populateChainConfigPresets` are unaffected.
- `showDetail(id)`: if `r.steps?.length > 1`, render each step's prompt + response into the new `detailSteps` container (and set `detailPrompt`/`detailResponse` from step 1 / last step as today); otherwise hide the container — current behavior unchanged.

### `tests/test_multi_step_presets.py` (new file)

Reuse the fixture pattern from `tests/test_benchmark_runner.py` (its `_LLMHandler` is class-level state, so define a **new handler class** here rather than importing, to avoid cross-file mode leakage):

- `RecordingHandler(BaseHTTPRequestHandler)`: class-level `requests: list[dict]` capturing each POST body; responds with the standard 2-chunk SSE stream + usage chunk, echoing a distinct marker per call (e.g. content includes `len(requests)` so step responses differ).
- Tests:
  1. **Multi-step execution order & context**: create endpoint + a 3-step preset (via `db.save_preset` or POST `/api/presets`), POST `/api/run`. Assert: 3 requests were made; request 2's `messages` = `[user(step1), assistant(resp1), user(step2)]`; request 3 includes resp1 and resp2 as assistant messages. Assert response JSON: `result["steps"]` has 3 entries with each step's prompt and response; `response` == last step's response; `completion_tokens` is the sum.
  2. **Persistence/history visibility**: after the run, `GET /api/results/{id}` returns decoded `steps` list with all prompts/responses.
  3. **Backward compat — legacy single-prompt preset**: save a `PromptPreset` with only `prompt` set (no steps); `GET /api/presets` shows `steps == [prompt]`; `/api/run` against it makes exactly **one** request whose `messages == [{"role":"user","content":prompt}]` and whose result has exactly one step entry.
  4. **DB round-trip**: `save_preset` with 2 steps → `list_presets()` / `presets_as_dict()` return both steps in order; `prompt` equals `steps[0]`.
  5. **Create/update API validation**: POST `/api/presets` with `steps` and empty `prompt` succeeds with `prompt == steps[0]`; POST with neither prompt nor steps → 400 (or 422 if validated in the schema — pick one and test it).
  6. **Mid-chain step failure** (optional but recommended): handler errors on the 2nd request; assert result is failed, `error_category` set, and `steps` contains only the completed first step.

Keep `tests/test_benchmark_runner.py` and `tests/test_chain_endpoints.py` untouched — they are the backward-compat regression net (single-prompt seed preset `simple` must keep passing as-is).

## Verification

1. `uv run pytest` — full suite green, including the untouched legacy tests (proves single-prompt compat).
2. Manual smoke (optional): `uv run python -m app.main`, create a 2-step preset in the UI, run it against a live endpoint, confirm both step responses appear in the run detail.

## Watch-outs

- `list_results_history`/`list_results_compact` select explicit columns — do NOT add `steps` there (keeps history payloads small); only `get_result` needs it for the detail view.
- The `presets` table migration must follow the existing try/except `ALTER TABLE` pattern; never edit the `CREATE TABLE` in a way that breaks existing DBs — add `steps TEXT NOT NULL DEFAULT '[]'` to both the `CREATE TABLE IF NOT EXISTS presets` / `results` statements (for fresh DBs) **and** the migration list (for existing DBs).
- `sqlite3.Row` → `dict` rows for `BenchmarkResult(**d)` will now include the raw JSON string in `steps`; decode before constructing (see `app/database.py` notes).
- Seed presets: no change required — decode-time fallback turns their empty `steps` into `[prompt]`.
