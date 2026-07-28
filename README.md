# LLM Benchmark Dashboard

A lightweight dashboard for benchmarking OpenAI-compatible LLM endpoints. Save endpoint configs, pick models, run preset benchmarks, and view results — all from a clean browser UI.

## Quick Start

```bash
# Install dependencies (one-time)
uv sync

# Start the server
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 9090
```

Or use the CLI entry point (available after `uv sync`):

```bash
llm-bench
```

> The `llm-bench` command is defined in `pyproject.toml` as `[project.scripts]` and delegates to `app.main:main`.

Then open **http://localhost:9090** in your browser.

### Background mode

```bash
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 9090 > /tmp/uvicorn.log 2>&1 &
```

## Features

- **Endpoint management** — add, edit, and delete OpenAI-compatible API endpoints (base URL, API key, extra headers)
- **Dynamic model listing** — fetches available models from any saved endpoint
- **5 benchmark presets** — Simple Echo, Code Generation, Logical Reasoning, Long Response, Translation
- **Live streaming** — measures time-to-first-token, total time, and tokens/second
- **Result history** — all runs persisted in SQLite
- **Dark-themed UI** — single-page app with Tailwind CSS
- **Swap Configs (LlamaSwap-style)** — save named configurations bundling endpoint, model, preset, and parameters for one-click re-runs
- **Compare mode** — select ≥2 past results and compare latency, token count, and throughput side by side
- **Charts & Trends** — time-series visualization of benchmark metrics, groupable by day or hour
- **Custom presets** — create, edit, and delete benchmark prompts beyond the 5 shipped presets
- **Chain benchmarks** — run multiple swap configs sequentially with live SSE progress tracking
- **Error classification** — HTTP errors, timeouts, and network failures are categorized automatically
- **CLI entry point** — run `llm-bench` after installation to start the dashboard directly

## Architecture

```
├── app/
│   ├── main.py        # FastAPI routes + benchmark runner
│   ├── database.py    # SQLite layer (endpoints, results, presets, swap_configs)
│   └── models.py      # Data classes
├── static/
│   ├── index.html     # Frontend (Tailwind CSS, no build step)
│   └── app.js         # Frontend logic
├── benchmarks.db      # Auto-created SQLite database
├── pyproject.toml     # Dependencies (uv) + CLI entry point (`llm-bench`)
└── uv.lock            # Locked dependency versions
```

### Database

The application uses a single SQLite file (`benchmarks.db`), auto-created on first run. Four tables are managed:

| Table | Description |
|---|---|
| `endpoints` | Saved API endpoint configurations |
| `results` | Benchmark run history with timing and token metrics |
| `presets` | User-defined and seeded benchmark prompts (5 shipped) |
| `swap_configs` | Named LlamaSwap-style saved configurations |
| `chain_runs` | Aggregated results for sequential chain benchmark executions |
| `chain_steps` | Per-step results within a chain run, including error classification |

No migration system is used; schema changes are handled via inline `ALTER TABLE` with graceful failure on duplicate columns.

## API Endpoints

### Presets

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/presets` | List benchmark presets |
| `POST` | `/api/presets` | Create a new preset |
| `PUT` | `/api/presets/{preset_id}` | Update an existing preset |
| `DELETE` | `/api/presets/{preset_id}` | Delete a preset |

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/endpoints` | List saved endpoints |
| `POST` | `/api/endpoints` | Create an endpoint |
| `PUT` | `/api/endpoints/{ep_id}` | Update an endpoint |
| `DELETE` | `/api/endpoints/{ep_id}` | Delete an endpoint |

### Models

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/models?endpoint_id=…` | Fetch available models from an endpoint |

### Swap Configs

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/swap-configs` | List saved swap configurations |
| `POST` | `/api/swap-configs` | Create a swap configuration |
| `PUT` | `/api/swap-configs/{cfg_id}` | Update a swap configuration |
| `DELETE` | `/api/swap-configs/{cfg_id}` | Delete a swap configuration |

### Benchmark

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/run` | Run a benchmark |

### Results

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/results` | List past results (filterable) |
| `GET` | `/api/results/{result_id}` | Get a single result by ID |
| `DELETE` | `/api/results/{result_id}` | Delete a result |
| `DELETE` | `/api/results` | Delete all results |

### History

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/history` | Compact result history (filterable, no prompt/response bodies) |

### Compare

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/compare` | Compare ≥2 results side by side |

### Summary & Trends

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/summary` | Aggregate summary statistics |
| `GET` | `/api/latest` | 20 most recent results (compact) |
| `GET` | `/api/trends` | Time-series trend data, groupable by day or hour |
| `GET` | `/api/best-worst` | Best and worst runs across key metrics |

### Chain Benchmarks

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/run-chain?stream=true` | Run chain with SSE streaming progress |
| `POST` | `/api/run-chain` | Run chain synchronously, returns final result |
| `GET` | `/api/chains` | List all chain runs |
| `GET` | `/api/chains/{chain_id}` | Get a specific chain run with step details |
| `DELETE` | `/api/chains/{chain_id}` | Delete a chain run and its steps |

The chain benchmark feature executes multiple swap configs in sequence, one after another. Each step can succeed or fail independently — partial failures do not abort the entire chain. SSE streaming (`?stream=true`) yields real-time events (`start`, `step`, `complete`, `error`) as each step completes.

#### Request

`POST /api/run-chain` accepts a JSON body:

```json
{
  "config_ids": ["cfg-abc123", "cfg-def456"]
}
```

#### Response (non-streaming)

```json
{
  "id": "chain-run-id",
  "total_steps": 2,
  "completed_steps": 1,
  "failed_steps": 1,
  "started_at": "2025-01-01T00:00:00",
  "finished_at": "2025-01-01T00:01:30",
  "steps": [
    {
      "step_index": 0,
      "config_id": "cfg-abc123",
      "config_name": "My Config",
      "model": "meta-llama/llama-3-70b",
      "success": true,
      "error": "",
      "error_category": null,
      "status_code": null,
      "benchmark_result": {
        "id": "result-id",
        "tokens_per_second": 42.5,
        "total_time_ms": 3200,
        "completion_tokens": 136
      }
    },
    {
      "step_index": 1,
      "config_id": "cfg-def456",
      "config_name": "Second Config",
      "model": "gpt-4",
      "success": false,
      "error": "Rate limited",
      "error_category": "http_error",
      "status_code": 429,
      "benchmark_result": null
    }
  ]
}
```

#### SSE Events (streaming)

| Event | Data |
|---|---|
| `start` | `{"chain_id": "..."}` |
| `step` | Step result with benchmark metrics or error info |
| `complete` | `{"completed_steps": N, "failed_steps": M}` |
| `error` | `{"message": "..."}` |

## Query Parameters

### `/api/results` and `/api/history`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `200` | Maximum number of results to return |
| `model` | str | — | Filter by model name |
| `preset` | str | — | Filter by preset name |
| `from_date` | str (ISO) | — | Include results on or after this date |
| `to_date` | str (ISO) | — | Include results on or before this date |

`/api/history` additionally accepts `endpoint` (str) to filter by endpoint ID.

### `/api/trends`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `model` | str | — | Filter by model name |
| `preset` | str | — | Filter by preset name |
| `endpoint` | str | — | Filter by endpoint ID |
| `from_date` | str (ISO) | — | Include results on or after this date |
| `to_date` | str (ISO) | — | Include results on or before this date |
| `group_by` | str | `day` | Group results by `day` or `hour` |

### `/api/models`

| Parameter | Type | Required | Description |
|---|---|---|---|
| `endpoint_id` | str | Yes | ID of the endpoint to fetch models from |

## Benchmark Run

### Request

`POST /api/run` accepts a JSON body:

```json
{
  "endpoint_id": "abc-123",
  "model": "gpt-4",
  "preset": "code",
  "max_tokens": 2048,
  "temperature": 0.7
}
```

### Response

The response is the full `BenchmarkResult` object, including:

- `total_time_ms` — total request duration in milliseconds
- `time_to_first_token_ms` — time until the first token was received
- `tokens_per_second` — generation throughput (excludes prompt processing time)
- `completion_tokens` — number of generated tokens
- `prompt_tokens` — number of prompt tokens
- `output_length` — character length of the generated output

## Requirements

- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) for dependency management

### Dependencies

| Package | Version | Purpose |
|---|---|---|
| `fastapi` | `>=0.115` | Web framework |
| `uvicorn[standard]` | `>=0.34` | ASGI server |
| `httpx` | `>=0.28` | Async HTTP client for LLM calls |
| `pydantic` | `>=2.10` | Request/response validation |
