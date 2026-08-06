"""Tests for multi-step prompt presets: multi-turn execution, persistence,
backward compatibility with single-prompt presets, and API validation."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import database as db
from app.models import EndpointConfig, PromptPreset


# ── Recording fake LLM handler ──────────────────────────────
# Deliberately a new class (not imported from test_benchmark_runner) to
# avoid cross-file mutable-state leakage.

class RecordingHandler(BaseHTTPRequestHandler):
    """Fake OpenAI endpoint that records every POST body and responds
    with a distinct marker per call so step responses differ."""
    requests: list[dict] = []
    error_on: int = 0  # 0 = no error; N = error on the Nth request (1-indexed)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        req = json.loads(body)
        RecordingHandler.requests.append(req)

        req_num = len(RecordingHandler.requests)
        if RecordingHandler.error_on and req_num == RecordingHandler.error_on:
            err_body = b'{"error": "simulated failure"}'
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
            return

        # Standard 2-chunk SSE stream + usage chunk
        chunks = [
            {"choices": [{"delta": {"content": f" step{req_num}"}}]},
            {"choices": [{"delta": {"content": f" done{req_num}"}}]},
            {"choices": [{"delta": {}}],
             "usage": {"prompt_tokens": 10 + req_num, "completion_tokens": 2, "total_tokens": 12 + req_num}},
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
def recording_server():
    server = HTTPServer(("127.0.0.1", 0), RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    RecordingHandler.requests.clear()
    RecordingHandler.error_on = 0
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


@pytest.fixture
def wired_endpoint(client, recording_server):
    """Create an endpoint pointing at the recording fake server."""
    ep = EndpointConfig(name="Recording", base_url=f"http://127.0.0.1:{recording_server.server_port}")
    db.save_endpoint(ep)
    return ep


# ── Test 1: Multi-step execution order & context ────────────

def test_multi_step_execution_context(client, wired_endpoint):
    """A 3-step preset must execute sequentially, threading messages."""
    ep = wired_endpoint
    # Create a 3-step preset via API
    r = client.post("/api/presets", json={
        "key": "multi3", "name": "Three Steps",
        "prompt": "Step one",
        "steps": ["Step one", "Follow up on that", "And one more thing"],
    })
    assert r.status_code == 200

    r = client.post("/api/run", json={
        "endpoint_id": ep.id, "model": "fake-model", "preset": "multi3",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True

    # Three requests were made
    assert len(RecordingHandler.requests) == 3

    # Request 1: single user message
    req1 = RecordingHandler.requests[0]
    assert req1["messages"] == [{"role": "user", "content": "Step one"}]

    # Request 2: includes step 1 response
    req2 = RecordingHandler.requests[1]
    assert req2["messages"] == [
        {"role": "user", "content": "Step one"},
        {"role": "assistant", "content": " step1 done1"},
        {"role": "user", "content": "Follow up on that"},
    ]

    # Request 3: includes both prior responses
    req3 = RecordingHandler.requests[2]
    assert req3["messages"] == [
        {"role": "user", "content": "Step one"},
        {"role": "assistant", "content": " step1 done1"},
        {"role": "user", "content": "Follow up on that"},
        {"role": "assistant", "content": " step2 done2"},
        {"role": "user", "content": "And one more thing"},
    ]

    # Result has 3 step entries
    assert len(body["steps"]) == 3
    assert body["steps"][0]["prompt"] == "Step one"
    assert body["steps"][1]["prompt"] == "Follow up on that"
    assert body["steps"][2]["prompt"] == "And one more thing"
    # Response is last step's response
    assert body["response"] == " step3 done3"
    # Completion tokens is sum
    assert body["completion_tokens"] == 6  # 2 + 2 + 2


# ── Test 2: Persistence / history visibility ────────────────

def test_result_persistence_with_steps(client, wired_endpoint):
    """GET /api/results/{id} returns decoded steps list."""
    ep = wired_endpoint
    client.post("/api/presets", json={
        "key": "persist_test", "name": "Persist Test",
        "steps": ["Hello", "How are you?"],
    })
    r = client.post("/api/run", json={
        "endpoint_id": ep.id, "model": "fake-model", "preset": "persist_test",
    })
    assert r.status_code == 200
    result_id = r.json()["id"]

    detail = client.get(f"/api/results/{result_id}").json()
    assert "steps" in detail
    assert len(detail["steps"]) == 2
    assert detail["steps"][0]["prompt"] == "Hello"
    assert detail["steps"][1]["prompt"] == "How are you?"


# ── Test 3: Backward compat — legacy single-prompt preset ───

def test_backward_compat_single_prompt(client, wired_endpoint):
    """A preset with only prompt (no steps) works as a single-step run."""
    ep = wired_endpoint
    # Save a preset with empty steps (simulates legacy data)
    p = PromptPreset(key="legacy", name="Legacy", prompt="Hello, how are you today?", steps=[])
    db.save_preset(p)

    # GET /api/presets should show steps == [prompt]
    presets = client.get("/api/presets").json()
    assert presets["legacy"]["steps"] == ["Hello, how are you today?"]

    # Run it
    r = client.post("/api/run", json={
        "endpoint_id": ep.id, "model": "fake-model", "preset": "legacy",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True

    # Exactly one request
    assert len(RecordingHandler.requests) == 1
    assert RecordingHandler.requests[0]["messages"] == [
        {"role": "user", "content": "Hello, how are you today?"}
    ]
    # Result has one step
    assert len(body["steps"]) == 1
    assert body["steps"][0]["prompt"] == "Hello, how are you today?"


# ── Test 4: DB round-trip ───────────────────────────────────

def test_db_roundtrip(client, _db_path):
    """save_preset with steps -> list_presets / presets_as_dict round-trip."""
    p = PromptPreset(
        key="roundtrip", name="Round Trip",
        prompt="First step",
        steps=["First step", "Second step"],
    )
    db.save_preset(p)

    loaded = db.list_presets()
    found = [x for x in loaded if x.key == "roundtrip"][0]
    assert found.steps == ["First step", "Second step"]
    assert found.prompt == "First step"

    adict = db.presets_as_dict()
    assert adict["roundtrip"]["steps"] == ["First step", "Second step"]
    assert adict["roundtrip"]["prompt"] == "First step"


# ── Test 5: Create/update API validation ────────────────────

def test_create_preset_steps_derive_prompt(client):
    """POST with steps and empty prompt -> prompt == steps[0]."""
    r = client.post("/api/presets", json={
        "key": "derived", "name": "Derived",
        "prompt": "",
        "steps": ["Alpha", "Beta"],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["prompt"] == "Alpha"
    assert body["steps"] == ["Alpha", "Beta"]


def test_create_preset_rejects_empty(client):
    """POST with neither prompt nor steps -> 400."""
    r = client.post("/api/presets", json={
        "key": "empty", "name": "Empty",
        "prompt": "", "steps": [],
    })
    assert r.status_code == 400


def test_update_preset_steps(client):
    """PUT with new steps updates the preset."""
    # Create first
    r = client.post("/api/presets", json={
        "key": "upd", "name": "Update Me", "prompt": "Original",
    })
    assert r.status_code == 200
    pid = r.json()["id"]

    # Update with new steps
    r = client.put(f"/api/presets/{pid}", json={
        "id": pid, "key": "upd", "name": "Update Me",
        "prompt": "", "steps": ["New one", "New two"],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["prompt"] == "New one"
    assert body["steps"] == ["New one", "New two"]


# ── Test 6: Mid-chain step failure ──────────────────────────

def test_mid_chain_step_failure(client, wired_endpoint):
    """If step 2 fails, result is failed with steps containing only step 1."""
    ep = wired_endpoint
    RecordingHandler.error_on = 2

    client.post("/api/presets", json={
        "key": "fail_test", "name": "Fail Test",
        "steps": ["Step A", "Step B", "Step C"],
    })

    r = client.post("/api/run", json={
        "endpoint_id": ep.id, "model": "fake-model", "preset": "fail_test",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["error_category"] == "http_error"
    # Only step 1 completed
    assert len(body["steps"]) == 1
    assert body["steps"][0]["prompt"] == "Step A"

    RecordingHandler.error_on = 0


# ── Regression: Defect 1 — TTFT measured at first token ─────

class _SlowTTFTHandler(BaseHTTPRequestHandler):
    """Fake LLM that sleeps ~0.15s before first content chunk, then
    ~0.15s before the second chunk, so TTFT is clearly under total_time.

    Uses chunked transfer encoding to stream incrementally so the client
    actually receives the first token before the second."""

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))  # consume body
        import time as _time
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        _time.sleep(0.15)  # delay before first token
        chunk1 = 'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n'
        self.wfile.write(chunk1.encode())
        self.wfile.flush()

        _time.sleep(0.15)  # delay before second token
        chunk2 = 'data: {"choices": [{"delta": {"content": " world"}}]}\n\n'
        chunk2 += 'data: {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}\n\n'
        chunk2 += "data: [DONE]\n\n"
        self.wfile.write(chunk2.encode())
        self.wfile.flush()

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
def slow_ttft_server():
    server = HTTPServer(("127.0.0.1", 0), _SlowTTFTHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def test_ttft_measured_at_first_token(client, slow_ttft_server):
    """TTFT must be measured at first-token arrival, not after the step completes.

    Regression test for defect #1: the multi-step refactoring replaced the
    in-stream TTFT measurement with a post-step monotonic read, making TTFT
    ~= total_time and corrupting tokens_per_second.
    """
    ep = EndpointConfig(name="SlowTTFT", base_url=slow_ttft_server)
    db.save_endpoint(ep)

    r = client.post("/api/run", json={
        "endpoint_id": ep.id, "model": "fake-model", "preset": "simple",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True

    ttft = body["time_to_first_token_ms"]
    total = body["total_time_ms"]

    # TTFT should be well under total_time (we sleep ~0.15s before first token,
    # then another ~0.15s before second, so total ~300ms+ and TTFT ~150ms)
    assert ttft < total - 100, f"TTFT {ttft:.0f}ms ~= total {total:.0f}ms — not measured at first token"
    assert ttft >= 80, f"TTFT {ttft:.0f}ms too small — handler delay not reflected"

    # tokens_per_second should use the generation window (total - ttft), not total
    # With ttft ~150ms and total ~300ms, generation ~150ms, so tps should be
    # significantly higher than completion_tokens / total_s
    if body["completion_tokens"] > 0 and total > 0:
        tps_from_total = body["completion_tokens"] / (total / 1000)
        assert body["tokens_per_second"] > 1.3 * tps_from_total, \
            f"TPS {body['tokens_per_second']:.1f} not using generation window (total-based={tps_from_total:.1f})"

    # Usage-based counts unchanged
    assert body["prompt_tokens"] == 5
    assert body["completion_tokens"] == 2


# ── Regression: Defect 2 — chain-run retrieval with steps ───

def test_chain_run_retrieval_with_steps(client, _db_path):
    """db.get_chain_run / GET /api/chains/{id} must not crash when a stored
    result has steps (list, not JSON string).

    Regression test for defect #2: _row_to_benchmark_result called json.loads
    on an already-decoded list, raising TypeError.
    """
    from app.models import ChainRunResult, ChainStepResult

    # Save a result with steps
    br = db.BenchmarkResult(
        endpoint_id="e", endpoint_name="E", model="m", preset_name="p",
        prompt="p1", response="r1", prompt_tokens=5, completion_tokens=2,
        total_tokens=7, time_to_first_token_ms=50.0, total_time_ms=100.0,
        tokens_per_second=20.0, output_length=2, created_at="2025-01-01T00:00:00",
        steps=[{"prompt": "p1", "response": "r1", "prompt_tokens": 5, "completion_tokens": 2, "total_time_ms": 100.0}],
    )
    db.save_result(br)

    # Create a chain run with a step referencing that result
    cr = ChainRunResult(
        config_ids=["cfg1"], total_steps=1, completed_steps=1, failed_steps=0,
        started_at="2025-01-01T00:00:00", finished_at="2025-01-01T00:01:00",
    )
    db.save_chain_run(cr)

    cs = ChainStepResult(
        step_index=0, config_id="cfg1", config_name="Cfg1", model="m",
        benchmark_result=br, error="", success=True,
    )
    db.save_chain_step(cs, cr.id)

    # Must not raise TypeError
    retrieved = db.get_chain_run(cr.id)
    assert retrieved is not None
    assert retrieved.step_results[0].benchmark_result is not None
    assert retrieved.step_results[0].benchmark_result.steps == [
        {"prompt": "p1", "response": "r1", "prompt_tokens": 5, "completion_tokens": 2, "total_time_ms": 100.0}
    ]

    # list_chain_runs also works
    runs = db.list_chain_runs()
    assert any(r.id == cr.id for r in runs)

    # HTTP surface: GET /api/chains/{id} returns 200
    r = client.get(f"/api/chains/{cr.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["step_results"][0]["benchmark_result"]["steps"] is not None


def test_row_to_benchmark_result_idempotent(_db_path):
    """_row_to_benchmark_result handles steps as str, list, or None."""
    # As JSON string
    br1 = db._row_to_benchmark_result({"steps": '[{"a":1}]'})
    assert br1.steps == [{"a": 1}]

    # As already-decoded list
    br2 = db._row_to_benchmark_result({"steps": [{"prompt": "hi"}]})
    assert br2.steps == [{"prompt": "hi"}]

    # As None / missing
    br3 = db._row_to_benchmark_result({"steps": None})
    assert br3.steps == []
    br4 = db._row_to_benchmark_result({})
    assert br4.steps == []
