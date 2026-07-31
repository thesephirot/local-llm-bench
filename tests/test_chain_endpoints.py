"""Endpoint-level tests for chain benchmark API — validation, error classification, SSE streaming."""
import json
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import ChainRunRequest, ErrorCategory


# ── Task 1: Input validation ────────────────────────────────

def test_input_validation_max_tokens_too_low(client):
    """max_tokens=0 should return HTTP 422."""
    r = client.post("/api/run", json={
        "endpoint_id": "fake", "model": "test", "preset": "simple",
        "max_tokens": 0, "temperature": 0.5,
    })
    assert r.status_code == 422


def test_input_validation_max_tokens_too_high(client):
    """max_tokens=200000 should return HTTP 422."""
    r = client.post("/api/run", json={
        "endpoint_id": "fake", "model": "test", "preset": "simple",
        "max_tokens": 200000, "temperature": 0.5,
    })
    assert r.status_code == 422


def test_input_validation_temperature_negative(client):
    """temperature=-0.1 should return HTTP 422."""
    r = client.post("/api/run", json={
        "endpoint_id": "fake", "model": "test", "preset": "simple",
        "max_tokens": 100, "temperature": -0.1,
    })
    assert r.status_code == 422


def test_input_validation_temperature_too_high(client):
    """temperature=3.0 should return HTTP 422."""
    r = client.post("/api/run", json={
        "endpoint_id": "fake", "model": "test", "preset": "simple",
        "max_tokens": 100, "temperature": 3.0,
    })
    assert r.status_code == 422


# ── Task: Chain validation ─────────────────────────────────

def test_run_chain_empty_config_ids(client):
    """Empty config_ids should return HTTP 400 (explicit validation in endpoint)."""
    r = client.post("/api/run-chain", json={"config_ids": []})
    assert r.status_code == 400
    assert "at least one config" in r.json()["detail"]


def test_run_chain_nonexistent_config(client):
    """POST with nonexistent config ID → step marked failed with error_category."""
    r = client.post("/api/run-chain", json={"config_ids": ["nonexistent-id"]})
    assert r.status_code == 200
    body = r.json()
    assert body["total_steps"] == 1
    assert body["failed_steps"] == 1
    assert body["completed_steps"] == 0
    step = body["steps"][0]
    assert not step["success"]
    assert "not found" in step["error"]
    assert step.get("error_category") == ErrorCategory.OTHER.value


# ── Task: Error classification ─────────────────────────────

def test_error_classification_http_status(client):
    """Mock _run_single_benchmark to raise HTTPStatusError → verify error_category."""
    # Create a real swap config so the chain doesn't fail at lookup
    from app import database as db
    ep_id = "test-ep-" + __import__("uuid").uuid4().hex[:8]
    cfg_id = "test-cfg-" + __import__("uuid").uuid4().hex[:8]

    ep = db.EndpointConfig(id=ep_id, name="Test", base_url="http://localhost:9999", api_key="x")
    db.save_endpoint(ep)
    from app.models import LlamaSwapConfig
    cfg = LlamaSwapConfig(id=cfg_id, name="TestCfg", endpoint_id=ep_id, endpoint_name="Test",
                          models=["test-model"], preset_key="simple")
    db.save_swap_config(cfg)

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "Rate limited"
    http_err = httpx.HTTPStatusError("Rate limited", request=MagicMock(), response=mock_resp)

    with patch("app.main._run_single_benchmark", new_callable=AsyncMock) as mock:
        mock.side_effect = http_err
        r = client.post("/api/run-chain", json={"config_ids": [cfg_id]})
        assert r.status_code == 200
        body = r.json()
        step = body["steps"][0]
        assert not step["success"]
        assert step["error_category"] == ErrorCategory.HTTP_ERROR.value
        assert step["status_code"] == 429

    # Cleanup
    db.delete_swap_config(cfg_id)
    db.delete_endpoint(ep_id)


def test_error_classification_timeout(client):
    """Mock _run_single_benchmark to raise TimeoutException → verify error_category."""
    from app import database as db
    ep_id = "test-ep-" + __import__("uuid").uuid4().hex[:8]
    cfg_id = "test-cfg-" + __import__("uuid").uuid4().hex[:8]

    ep = db.EndpointConfig(id=ep_id, name="Test", base_url="http://localhost:9999", api_key="x")
    db.save_endpoint(ep)
    from app.models import LlamaSwapConfig
    cfg = LlamaSwapConfig(id=cfg_id, name="TestCfg", endpoint_id=ep_id, endpoint_name="Test",
                          models=["test-model"], preset_key="simple")
    db.save_swap_config(cfg)

    with patch("app.main._run_single_benchmark", new_callable=AsyncMock) as mock:
        mock.side_effect = httpx.TimeoutException("timed out")
        r = client.post("/api/run-chain", json={"config_ids": [cfg_id]})
        assert r.status_code == 200
        body = r.json()
        step = body["steps"][0]
        assert not step["success"]
        assert step["error_category"] == ErrorCategory.TIMEOUT.value

    db.delete_swap_config(cfg_id)
    db.delete_endpoint(ep_id)


def test_error_classification_network(client):
    """Mock _run_single_benchmark to raise ConnectError → verify error_category."""
    from app import database as db
    ep_id = "test-ep-" + __import__("uuid").uuid4().hex[:8]
    cfg_id = "test-cfg-" + __import__("uuid").uuid4().hex[:8]

    ep = db.EndpointConfig(id=ep_id, name="Test", base_url="http://localhost:9999", api_key="x")
    db.save_endpoint(ep)
    from app.models import LlamaSwapConfig
    cfg = LlamaSwapConfig(id=cfg_id, name="TestCfg", endpoint_id=ep_id, endpoint_name="Test",
                          models=["test-model"], preset_key="simple")
    db.save_swap_config(cfg)

    with patch("app.main._run_single_benchmark", new_callable=AsyncMock) as mock:
        mock.side_effect = httpx.ConnectError("connection refused")
        r = client.post("/api/run-chain", json={"config_ids": [cfg_id]})
        assert r.status_code == 200
        body = r.json()
        step = body["steps"][0]
        assert not step["success"]
        assert step["error_category"] == ErrorCategory.NETWORK.value

    db.delete_swap_config(cfg_id)
    db.delete_endpoint(ep_id)


# ── Task: SSE streaming ────────────────────────────────────

def test_sse_stream_start_event(client):
    """POST /api/run-chain?stream=true with nonexistent config → first event is 'start'."""
    r = client.post("/api/run-chain?stream=true", json={"config_ids": ["nonexistent"]})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    text = r.text
    assert text.startswith("event: start")


def test_sse_stream_complete_event(client):
    """SSE stream ends with a 'complete' event."""
    r = client.post("/api/run-chain?stream=true", json={"config_ids": ["nonexistent"]})
    assert r.status_code == 200
    text = r.text
    assert "event: complete" in text


def test_chain_crud_via_api(client):
    """Create chain run via DB, verify list/get/delete work."""
    from app import database as db
    from app.models import ChainRunResult, ChainStepResult

    # Create a chain run directly in the DB
    cr = ChainRunResult(
        config_ids=["cfg1"], total_steps=1, completed_steps=0, failed_steps=1,
        started_at="2025-01-01T00:00:00", finished_at="2025-01-01T00:01:00",
    )
    db.save_chain_run(cr)

    # GET /api/chains/{id}
    r = client.get(f"/api/chains/{cr.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["total_steps"] == 1

    # GET /api/chains
    r = client.get("/api/chains")
    assert r.status_code == 200
    chains = r.json()
    assert any(c["id"] == cr.id for c in chains)

    # DELETE /api/chains/{id}
    r = client.delete(f"/api/chains/{cr.id}")
    assert r.status_code == 200

    # Verify deleted
    r = client.get(f"/api/chains/{cr.id}")
    assert r.status_code == 404


# ── Chain status & cancellation ─────────────────────────────

def test_chain_status_empty_when_idle(client):
    r = client.get("/api/chain-status")
    assert r.status_code == 200
    assert r.json() == []


def test_cancel_chain_not_running(client):
    r = client.post("/api/chains/does-not-exist/cancel")
    assert r.status_code == 409


def test_chain_status_reports_interrupted_for_stale_chain(client):
    """An unfinished chain with no heartbeat must show as interrupted."""
    from app.models import ChainRunResult
    from app import database as db
    cr = ChainRunResult(config_ids=["x"], total_steps=2,
                        started_at="2026-01-01T00:00:00")
    db.save_chain_run(cr)
    r = client.get("/api/chain-status")
    assert r.status_code == 200
    states = {c["chain_id"]: c["state"] for c in r.json()}
    assert states.get(cr.id) == "interrupted"
