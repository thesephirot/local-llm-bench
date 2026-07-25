# LLM Benchmark Dashboard

A lightweight dashboard for benchmarking OpenAI-compatible LLM endpoints. Save endpoint configs, pick models, run preset benchmarks, and view results — all from a clean browser UI.

## Quick Start

```bash
# Install dependencies (one-time)
uv sync

# Start the server
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 9090
```

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

## Architecture

```
├── app/
│   ├── main.py        # FastAPI routes + benchmark runner
│   ├── database.py    # SQLite layer (endpoints + results)
│   └── models.py      # Data classes
├── static/
│   └── index.html     # Frontend (Tailwind CSS, no build step)
├── benchmarks.db      # Auto-created SQLite database
└── pyproject.toml     # Dependencies (uv)
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Dashboard UI |
| `GET` | `/api/presets` | List benchmark presets |
| `GET` | `/api/endpoints` | List saved endpoints |
| `POST` | `/api/endpoints` | Create endpoint |
| `PUT` | `/api/endpoints/:id` | Update endpoint |
| `DELETE` | `/api/endpoints/:id` | Delete endpoint |
| `GET` | `/api/models?endpoint_id=…` | Fetch models from endpoint |
| `POST` | `/api/run` | Run a benchmark |
| `GET` | `/api/results` | List past results |
| `DELETE` | `/api/results/:id` | Delete a result |

## Requirements

- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) for dependency management
