#!/usr/bin/env bash
# Start the LLM Benchmark Dashboard.
# Usage:
#   ./start.sh              # foreground, http://127.0.0.1:9090
#   ./start.sh --bg         # background, logs to /tmp/llm-bench.log
#   HOST=0.0.0.0 PORT=8080 ./start.sh   # override bind address
set -euo pipefail

cd "$(dirname "$0")"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-9090}"

# Install deps if the venv is missing
if [ ! -x .venv/bin/uvicorn ]; then
    echo "Virtualenv not found — running 'uv sync' first..."
    uv sync
fi

if [ "${1:-}" = "--bg" ]; then
    echo "Starting in background on http://${HOST}:${PORT} (log: /tmp/llm-bench.log)"
    nohup .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT" \
        > /tmp/llm-bench.log 2>&1 &
    echo "PID: $!"
else
    echo "Starting on http://${HOST}:${PORT} (Ctrl+C to stop)"
    exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT"
fi
