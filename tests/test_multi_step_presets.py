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
