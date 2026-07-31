"""Regression tests for review findings: real failure paths, usage-based
token counting, SSE framing, and stats exclusion of failed runs.

These tests spin up a real local HTTP server so the full
`_run_single_benchmark` code path executes — no mocking of the benchmark
core, which previously masked the HTTP-error misclassification bug.
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import database as db
from app.models import BenchmarkResult, EndpointConfig, LlamaSwapConfig


class _LLMHandler(BaseHTTPRequestHandler):
    """Configurable fake OpenAI-compatible endpoint."""

    status = 200
    mode = "stream"  # "stream" | "error"

    def do_POST(self):
        if self.mode == "error":
            body = b'{"error": "rate limited"}'
            self.send_response(self.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        chunks = [
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " world"}}]},
            {"choices": [{"delta": {}}],
             "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}},
        ]
        body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        payload = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        body = json.dumps({"data": [{"id": "fake-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def llm_server():
    server = HTTPServer(("127.0.0.1", 0), _LLMHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


@pytest.fixture
def wired_config(client, llm_server):
    """Create an endpoint + swap config pointing at the fake LLM server."""
    ep = EndpointConfig(name="Fake", base_url=llm_server)
    db.save_endpoint(ep)
    cfg = LlamaSwapConfig(name="FakeCfg", endpoint_id=ep.id, endpoint_name=ep.name,
                          model="fake-model", preset_key="simple")
    db.save_swap_config(cfg)
    return ep, cfg


# ── Real-path failure classification (review bug #1) ───────

def test_chain_http_error_real_path(client, wired_config):
    """HTTP errors from the LLM must mark the chain step as failed.

    Regression test: previously _run_single_benchmark swallowed
    HTTPStatusError and returned a 'successful' result with tps=0, so the
    chain's error classification was dead code outside of mocked tests.
    """
    _LLMHandler.mode = "error"
    _LLMHandler.status = 429
    try:
        ep, cfg = wired_config
        r = client.post("/api/run-chain", json={"config_ids": [cfg.id]})
        assert r.status_code == 200
        body = r.json()
        assert body["completed_steps"] == 0
        assert body["failed_steps"] == 1
        step = body["steps"][0]
        assert not step["success"]
        assert step["error_category"] == "http_error"
        assert step["status_code"] == 429
        assert "429" in step["error"]

        # The failed run must be persisted as a failed result, not a success.
        result = db.get_result(db.list_results()[0].id)
        assert result["success"] == 0
        assert result["error_category"] == "http_error"
    finally:
        _LLMHandler.mode = "stream"
        _LLMHandler.status = 200


def test_chain_network_error_real_path(client, _db_path):
    """Connection refused must classify as a network error via the real path."""
    ep = EndpointConfig(name="Down", base_url="http://127.0.0.1:1")
    db.save_endpoint(ep)
    cfg = LlamaSwapConfig(name="DownCfg", endpoint_id=ep.id, endpoint_name=ep.name,
                          model="m", preset_key="simple")
    db.save_swap_config(cfg)

    r = client.post("/api/run-chain", json={"config_ids": [cfg.id]})
    assert r.status_code == 200
    step = r.json()["steps"][0]
    assert not step["success"]
    assert step["error_category"] == "network"


# ── Usage-based token counting (review bug #3) ─────────────

def test_run_uses_server_usage_tokens(client, wired_config):
    """When the server streams a usage chunk, token counts must be real."""
    ep, cfg = wired_config
    r = client.post("/api/run", json={
        "endpoint_id": ep.id, "model": "fake-model", "preset": "simple",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["response"] == "Hello world"
    assert body["prompt_tokens"] == 5
    assert body["completion_tokens"] == 2
    assert body["tokens_estimated"] is False


def test_run_failure_returns_discriminated_result(client, wired_config):
    """/api/run returns success=False with error details on HTTP failure."""
    _LLMHandler.mode = "error"
    _LLMHandler.status = 500
    try:
        ep, cfg = wired_config
        r = client.post("/api/run", json={
            "endpoint_id": ep.id, "model": "fake-model", "preset": "simple",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is False
        assert body["error_category"] == "http_error"
        assert body["status_code"] == 500
        assert "500" in body["error"]
    finally:
        _LLMHandler.mode = "stream"
        _LLMHandler.status = 200


# ── SSE framing (review bug #2) ────────────────────────────

def test_sse_start_event_is_valid_json(client):
    """The SSE 'start' event data must parse as JSON (was double-quoted)."""
    r = client.post("/api/run-chain?stream=true", json={"config_ids": ["missing"]})
    assert r.status_code == 200
    events = [e for e in r.text.split("\n\n") if e.strip()]
    first = events[0]
    assert first.startswith("event: start")
    data_line = [l for l in first.split("\n") if l.startswith("data: ")][0]
    payload = json.loads(data_line[6:])  # must not raise
    assert isinstance(payload["chain_id"], str)
    assert payload["chain_id"]


def test_sse_stream_events_all_valid_json(client, wired_config):
    """Every event in a streamed chain must carry valid JSON data."""
    ep, cfg = wired_config
    r = client.post("/api/run-chain?stream=true", json={"config_ids": [cfg.id]})
    assert r.status_code == 200
    events = [e for e in r.text.split("\n\n") if e.strip()]
    kinds = []
    for e in events:
        lines = e.split("\n")
        kind = [l[7:] for l in lines if l.startswith("event: ")][0]
        data = [l[6:] for l in lines if l.startswith("data: ")][0]
        parsed = json.loads(data)  # must not raise
        kinds.append(kind)
        if kind == "step":
            assert parsed["success"] is True
            assert parsed["benchmark_result"]["completion_tokens"] == 2
    assert kinds == ["start", "step", "complete"]


# ── Stats exclusion of failed runs (review bug #1 fallout) ─

def _make_result(**kw):
    defaults = dict(
        endpoint_id="e", endpoint_name="E", model="m", preset_name="p",
        prompt="p", response="r", prompt_tokens=10, completion_tokens=100,
        total_tokens=110, time_to_first_token_ms=50.0, total_time_ms=1000.0,
        tokens_per_second=100.0, output_length=1, created_at="2025-01-01T00:00:00",
    )
    defaults.update(kw)
    return BenchmarkResult(**defaults)


def test_failed_runs_excluded_from_summary(client, _db_path):
    db.save_result(_make_result(tokens_per_second=100.0))
    db.save_result(_make_result(success=False, error="boom", error_category="http_error",
                                tokens_per_second=0.0, total_time_ms=0.0,
                                time_to_first_token_ms=0.0))

    s = client.get("/api/summary").json()
    assert s["total_runs"] == 2
    assert s["failed_runs"] == 1
    assert s["avg_tps"] == 100.0        # not dragged down by the failed run
    assert s["worst_tps"] == 100.0      # not 0.0 from the failed run

    bw = client.get("/api/best-worst").json()
    assert bw["worst_tps"]["tokens_per_second"] == 100.0

    trends = client.get("/api/trends").json()
    assert sum(t["count"] for t in trends) == 1


def test_summary_all_failed(client, _db_path):
    """Summary must not crash when every run failed (AVG over empty set)."""
    db.save_result(_make_result(success=False, error="boom", tokens_per_second=0.0))
    s = client.get("/api/summary").json()
    assert s["total_runs"] == 1
    assert s["failed_runs"] == 1
    assert s["avg_tps"] == 0
    assert client.get("/api/best-worst").json() == {}
