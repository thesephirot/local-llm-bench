from __future__ import annotations

import json
import time
import datetime
import logging
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from .models import EndpointConfig, PromptPreset, LlamaSwapConfig, BenchmarkResult, ChainRunRequest, ChainStepResult, ChainRunResult, ErrorCategory
from . import database as db

_db_sync = db._db_sync  # re-export for use in SSE generator
db.init_db()

app = FastAPI(title="LLM Benchmark Dashboard")

# Serve static frontend
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── Request/Response schemas ────────────────────────────────

class EndpointCreate(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    extra_headers: str = "{}"


class EndpointUpdate(BaseModel):
    id: str
    name: str
    base_url: str
    api_key: str = ""
    extra_headers: str = "{}"


class BenchmarkRun(BaseModel):
    endpoint_id: str
    model: str
    preset: str
    max_tokens: int = Field(default=2048, ge=1, le=131072)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


class ModelItem(BaseModel):
    id: str
    name: str


class PresetCreate(BaseModel):
    key: str
    name: str
    prompt: str
    description: str = ""


class PresetUpdate(BaseModel):
    id: str
    key: str
    name: str
    prompt: str
    description: str = ""


class SwapConfigCreate(BaseModel):
    name: str
    endpoint_id: str
    model: str
    preset_key: str = ""
    max_tokens: int = 2048
    temperature: float = 0.7
    notes: str = ""


class SwapConfigUpdate(BaseModel):
    id: str
    name: str
    endpoint_id: str
    model: str
    preset_key: str = ""
    max_tokens: int = 2048
    temperature: float = 0.7
    notes: str = ""


class ComparisonRequest(BaseModel):
    result_ids: list[str]


# ── Shared Chain Execution ────────────────────────────────

def _step_to_dict(step: ChainStepResult) -> dict:
    """Serialize a chain step for API responses and SSE events."""
    data = {
        "step_index": step.step_index,
        "config_id": step.config_id,
        "config_name": step.config_name,
        "model": step.model,
        "success": step.success,
        "error": step.error,
        "error_category": step.error_category or None,
        "status_code": step.status_code,
        "benchmark_result": None,
    }
    if step.success and step.benchmark_result:
        br = step.benchmark_result
        data["benchmark_result"] = {
            "id": br.id,
            "tokens_per_second": br.tokens_per_second,
            "total_time_ms": br.total_time_ms,
            "completion_tokens": br.completion_tokens,
            "prompt_tokens": br.prompt_tokens,
        }
    return data


async def _execute_chain(config_ids: list[str]):
    """Shared chain execution core.

    Async generator yielding ("start", dict), ("step", ChainStepResult),
    and ("complete", dict) events. Used by both the SSE streaming endpoint
    and the synchronous endpoint so the execution logic lives in one place.
    """

    # Resolve configs and build ordered list of steps
    steps: list[ChainStepResult] = []
    for idx, cfg_id in enumerate(config_ids):
        cfg = await _db_sync(db.get_swap_config, cfg_id)
        if not cfg:
            steps.append(ChainStepResult(step_index=idx, config_id=cfg_id,
                                         error=f"Config {cfg_id} not found", success=False,
                                         error_category=ErrorCategory.OTHER.value))
            continue
        steps.append(ChainStepResult(step_index=idx, config_id=cfg.id,
                                     config_name=cfg.name, model=cfg.model))

    # Create and persist the chain run record
    chain_result = ChainRunResult(
        config_ids=config_ids,
        step_results=[],
        total_steps=len(steps),
        started_at=datetime.datetime.now().isoformat(),
    )
    await _db_sync(db.save_chain_run, chain_result)

    yield ("start", {"chain_id": chain_result.id, "started_at": chain_result.started_at})

    # Resolve presets once
    presets = await _db_sync(db.presets_as_dict)

    completed = 0
    failed = 0

    for step in steps:
        cfg = await _db_sync(db.get_swap_config, step.config_id)
        if not cfg or not cfg.endpoint_id:
            step.error = step.error or f"Config {step.config_id} not found"
            step.success = False
            step.error_category = ErrorCategory.OTHER.value
        else:
            ep = await _db_sync(db.get_endpoint, cfg.endpoint_id)
            if not ep:
                step.error = "Endpoint not found"
                step.success = False
                step.error_category = ErrorCategory.OTHER.value
            else:
                preset_name = cfg.preset_key or "simple"
                preset = presets.get(preset_name, {})
                try:
                    br = await _run_single_benchmark(
                        endpoint=ep,
                        model=cfg.model,
                        preset_name=preset.get("name", preset_name),
                        preset_prompt=preset.get("prompt", "Hello, how are you today?"),
                        max_tokens=cfg.max_tokens,
                        temperature=cfg.temperature,
                    )
                    # _run_single_benchmark returns a discriminated result:
                    # failures (HTTP/timeout/network) come back with success=False.
                    step.benchmark_result = br
                    step.success = br.success
                    if not br.success:
                        step.error = br.error
                        step.error_category = br.error_category or ErrorCategory.OTHER.value
                        step.status_code = br.status_code
                except httpx.TimeoutException as e:
                    step.error = str(e)
                    step.error_category = ErrorCategory.TIMEOUT.value
                    step.success = False
                except (httpx.ConnectError, httpx.NetworkError) as e:
                    step.error = str(e)
                    step.error_category = ErrorCategory.NETWORK.value
                    step.success = False
                except httpx.HTTPStatusError as e:
                    step.error = f"HTTP {e.response.status_code}: {e.response.text}"
                    step.error_category = ErrorCategory.HTTP_ERROR.value
                    step.status_code = e.response.status_code
                    step.success = False
                except Exception as e:
                    step.error = str(e)
                    step.error_category = ErrorCategory.OTHER.value
                    step.success = False

        if step.success:
            completed += 1
        else:
            failed += 1
        await _db_sync(db.save_chain_step, step, chain_result.id)
        yield ("step", step)

    chain_result.finished_at = datetime.datetime.now().isoformat()
    chain_result.completed_steps = completed
    chain_result.failed_steps = failed
    await _db_sync(db.save_chain_run, chain_result)

    yield ("complete", {"completed_steps": completed, "failed_steps": failed,
                        "finished_at": chain_result.finished_at})


async def _stream_chain_execution(config_ids: list[str]):
    """Format chain execution events as SSE frames."""
    async for kind, payload in _execute_chain(config_ids):
        if kind == "step":
            data = _step_to_dict(payload)
        else:
            data = payload
        yield f"event: {kind}\ndata: {json.dumps(data)}\n\n"


# ── Shared Benchmark Logic ─────────────────────────────────

async def _run_single_benchmark(
    endpoint: EndpointConfig,
    model: str,
    preset_name: str,
    preset_prompt: str,
    max_tokens: int = 2048,
    temperature: float = 0.7,
) -> BenchmarkResult:
    """Run a single streaming benchmark and return the result.

    This is the shared core logic used by both /api/run and /api/run-chain.
    """
    headers = {"Content-Type": "application/json"}
    if endpoint.api_key:
        # Local servers (llama.cpp/llama-swap) often have no key; sending
        # "Bearer " with an empty token is an illegal HTTP header value.
        headers["Authorization"] = f"Bearer {endpoint.api_key}"
    try:
        extra = json.loads(endpoint.extra_headers)
        headers.update(extra)
    except json.JSONDecodeError:
        pass

    result = BenchmarkResult(
        endpoint_id=endpoint.id,
        endpoint_name=endpoint.name,
        model=model,
        preset_name=preset_name,
        prompt=preset_prompt,
    )

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": preset_prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        # Ask the server to include a usage chunk so token counts are
        # measured rather than estimated (ignored by servers that don't
        # support it).
        "stream_options": {"include_usage": True},
    }

    async def _fail(error: str, category: ErrorCategory, status_code: int | None = None,
                    start: float | None = None) -> BenchmarkResult:
        """Persist and return a failed result so failures are visible in history."""
        result.success = False
        result.error = error
        result.error_category = category.value
        result.status_code = status_code
        if start is not None:
            result.total_time_ms = (time.monotonic() - start) * 1000
        result.created_at = datetime.datetime.now().isoformat()
        await _db_sync(db.save_result, result)
        return result

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            first_token_time = None
            full_response = []
            usage_seen = False
            done = False
            async with client.stream("POST", f"{endpoint.base_url}/v1/chat/completions",
                                     json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    # Read the body so .text is available for the error report.
                    await resp.aread()
                    resp.raise_for_status()
                async for chunk_bytes in resp.aiter_bytes():
                    if done:
                        break
                    text = chunk_bytes.decode().strip()
                    if not text:
                        continue
                    for line in text.split("\n"):
                        if not line.startswith("data: "):
                            continue
                        json_str = line[6:]
                        if json_str == "[DONE]":
                            done = True
                            break
                        try:
                            chunk = json.loads(json_str)
                            content = (
                                chunk.get("choices", [{}])[0]
                                .get("delta", {})
                                .get("content", "")
                            )
                            if content:
                                if first_token_time is None:
                                    first_token_time = (time.monotonic() - start) * 1000
                                full_response.append(content)

                            usage = chunk.get("usage")
                            if usage:
                                usage_seen = True
                                result.prompt_tokens = usage.get("prompt_tokens", 0)
                                result.completion_tokens = usage.get("completion_tokens", 0)
                                result.total_tokens = usage.get("total_tokens", 0)
                        except json.JSONDecodeError:
                            continue
    except httpx.HTTPStatusError as e:
        return await _fail(f"HTTP {e.response.status_code}: {e.response.text}",
                           ErrorCategory.HTTP_ERROR, e.response.status_code, start)
    except httpx.TimeoutException as e:
        return await _fail(str(e), ErrorCategory.TIMEOUT, start=start)
    except (httpx.ConnectError, httpx.NetworkError) as e:
        return await _fail(str(e), ErrorCategory.NETWORK, start=start)

    result.response = "".join(full_response)
    result.output_length = len(result.response)
    result.total_time_ms = (time.monotonic() - start) * 1000
    result.time_to_first_token_ms = first_token_time or 0

    # Fallback token estimation when the API doesn't return usage.
    if not usage_seen:
        result.tokens_estimated = True
        if result.completion_tokens == 0:
            result.completion_tokens = max(1, len(result.response) // 4)
        if result.prompt_tokens == 0:
            result.prompt_tokens = max(1, len(preset_prompt) // 4)
    result.total_tokens = result.prompt_tokens + result.completion_tokens

    generation_ms = result.total_time_ms - (result.time_to_first_token_ms or 0)
    if generation_ms > 0:
        result.tokens_per_second = result.completion_tokens / (generation_ms / 1000)
    elif result.total_time_ms > 0:
        result.tokens_per_second = result.completion_tokens / (result.total_time_ms / 1000)
    result.created_at = datetime.datetime.now().isoformat()

    await _db_sync(db.save_result, result)
    return result


# ── API Routes ──────────────────────────────────────────────

@app.get("/favicon.ico")
async def favicon():
    from fastapi.responses import Response
    return Response(status_code=204)


@app.get("/")
async def index():
    return FileResponse("static/index.html")


# ── Presets ─────────────────────────────────────────────────

@app.get("/api/presets")
async def get_presets():
    return db.presets_as_dict()


@app.post("/api/presets")
async def create_preset(data: PresetCreate):
    p = PromptPreset(key=data.key, name=data.name, prompt=data.prompt, description=data.description)
    return db.save_preset(p)


@app.put("/api/presets/{preset_id}")
async def update_preset(preset_id: str, data: PresetUpdate):
    p = PromptPreset(id=data.id, key=data.key, name=data.name, prompt=data.prompt, description=data.description)
    return db.save_preset(p)


@app.delete("/api/presets/{preset_id}")
async def delete_preset(preset_id: str):
    db.delete_preset(preset_id)
    return {"ok": True}


# ── Endpoints ───────────────────────────────────────────────

@app.get("/api/endpoints")
async def get_endpoints():
    return db.list_endpoints()


@app.post("/api/endpoints")
async def create_endpoint(data: EndpointCreate):
    ep = EndpointConfig(name=data.name, base_url=data.base_url.rstrip("/"),
                        api_key=data.api_key, extra_headers=data.extra_headers)
    return db.save_endpoint(ep)


@app.put("/api/endpoints/{ep_id}")
async def update_endpoint(ep_id: str, data: EndpointUpdate):
    ep = EndpointConfig(id=data.id, name=data.name, base_url=data.base_url.rstrip("/"),
                        api_key=data.api_key, extra_headers=data.extra_headers)
    return db.save_endpoint(ep)


@app.delete("/api/endpoints/{ep_id}")
async def delete_endpoint(ep_id: str):
    db.delete_endpoint(ep_id)
    return {"ok": True}


@app.get("/api/models")
async def get_models(endpoint_id: str):
    ep = db.get_endpoint(endpoint_id)
    if not ep:
        raise HTTPException(404, "Endpoint not found")

    headers = {}
    if ep.api_key:
        headers["Authorization"] = f"Bearer {ep.api_key}"
    try:
        extra = json.loads(ep.extra_headers)
        headers.update(extra)
    except json.JSONDecodeError:
        pass

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{ep.base_url}/v1/models", headers=headers)
            r.raise_for_status()
            data = r.json()
            models = data.get("data", [])
            return [ModelItem(id=m.get("id", ""), name=m.get("id", "")) for m in models]
        except httpx.HTTPStatusError as e:
            raise HTTPException(e.response.status_code, f"API error: {e.response.text}")
        except Exception as e:
            raise HTTPException(502, f"Failed to reach endpoint: {e}")


# ── Swap Configs ────────────────────────────────────────────

@app.get("/api/swap-configs")
async def get_swap_configs():
    return db.list_swap_configs()


@app.post("/api/swap-configs")
async def create_swap_config(data: SwapConfigCreate):
    ep = db.get_endpoint(data.endpoint_id)
    if not ep:
        raise HTTPException(404, "Endpoint not found")
    presets = db.presets_as_dict()
    preset = presets.get(data.preset_key, {})
    cfg = LlamaSwapConfig(
        name=data.name,
        endpoint_id=data.endpoint_id,
        endpoint_name=ep.name,
        model=data.model,
        preset_key=data.preset_key,
        preset_name=preset.get("name", data.preset_key),
        max_tokens=data.max_tokens,
        temperature=data.temperature,
        notes=data.notes,
        created_at=datetime.datetime.now().isoformat(),
    )
    return db.save_swap_config(cfg)


@app.put("/api/swap-configs/{cfg_id}")
async def update_swap_config(cfg_id: str, data: SwapConfigUpdate):
    ep = db.get_endpoint(data.endpoint_id)
    presets = db.presets_as_dict()
    preset = presets.get(data.preset_key, {})
    cfg = LlamaSwapConfig(
        id=data.id,
        name=data.name,
        endpoint_id=data.endpoint_id,
        endpoint_name=ep.name if ep else "",
        model=data.model,
        preset_key=data.preset_key,
        preset_name=preset.get("name", data.preset_key),
        max_tokens=data.max_tokens,
        temperature=data.temperature,
        notes=data.notes,
        created_at=datetime.datetime.now().isoformat(),
    )
    return db.save_swap_config(cfg)


@app.delete("/api/swap-configs/{cfg_id}")
async def delete_swap_config(cfg_id: str):
    db.delete_swap_config(cfg_id)
    return {"ok": True}


# ── Benchmark Run ───────────────────────────────────────────

@app.post("/api/run")
async def run_benchmark(data: BenchmarkRun):
    ep = db.get_endpoint(data.endpoint_id)
    if not ep:
        raise HTTPException(404, "Endpoint not found")

    presets = db.presets_as_dict()
    preset = presets.get(data.preset)
    if not preset:
        raise HTTPException(400, f"Unknown preset: {data.preset}")

    result = await _run_single_benchmark(
        endpoint=ep,
        model=data.model,
        preset_name=preset["name"],
        preset_prompt=preset["prompt"],
        max_tokens=data.max_tokens,
        temperature=data.temperature,
    )
    return result


# ── Results ─────────────────────────────────────────────────

@app.get("/api/results")
async def get_results(
    limit: int = 200,
    model: str | None = None,
    preset: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
):
    return db.list_results_filter(limit, model, preset, from_date, to_date)


@app.get("/api/history")
async def get_history(
    limit: int = 200,
    model: str | None = None,
    preset: str | None = None,
    endpoint: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
):
    return db.list_results_history(limit, model, preset, endpoint, from_date, to_date)


@app.get("/api/results/{result_id}")
async def get_result(result_id: str):
    result = db.get_result(result_id)
    if not result:
        raise HTTPException(404, "Result not found")
    return result


@app.delete("/api/results/{result_id}")
async def delete_result(result_id: str):
    db.delete_result(result_id)
    return {"ok": True}


@app.delete("/api/results")
async def delete_all_results():
    db.delete_all_results()
    return {"ok": True}


# ── Comparison ──────────────────────────────────────────────

@app.post("/api/compare")
async def compare(data: ComparisonRequest):
    if len(data.result_ids) < 2:
        raise HTTPException(400, "Need at least 2 results to compare")
    results = db.compare_results(data.result_ids)
    if len(results) < 2:
        raise HTTPException(400, "Not enough valid results found")
    return results


# ── Chain Benchmark Run ─────────────────────────────────────

@app.post("/api/run-chain")
async def run_chain(data: ChainRunRequest, stream: bool = Query(default=False)):
    """Execute a sequential chain of benchmark runs, one LLM after another."""
    if not data.config_ids:
        raise HTTPException(400, "Chain must contain at least one config")

    if stream:
        return StreamingResponse(
            _stream_chain_execution(data.config_ids),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    # Non-streaming: drain the shared execution generator and return the final result.
    steps: list[ChainStepResult] = []
    info: dict = {}
    async for kind, payload in _execute_chain(data.config_ids):
        if kind == "step":
            steps.append(payload)
        else:
            info[kind] = payload

    return {
        "id": info["start"]["chain_id"],
        "total_steps": len(steps),
        "completed_steps": info["complete"]["completed_steps"],
        "failed_steps": info["complete"]["failed_steps"],
        "started_at": info["start"]["started_at"],
        "finished_at": info["complete"]["finished_at"],
        "steps": [_step_to_dict(s) for s in steps],
    }


# ── Chain Run Management ────────────────────────────────────

@app.get("/api/chains")
async def list_chains(limit: int = 100):
    return db.list_chain_runs(limit)


@app.get("/api/chains/{chain_id}")
async def get_chain(chain_id: str):
    cr = db.get_chain_run(chain_id)
    if not cr:
        raise HTTPException(404, "Chain run not found")
    return cr


@app.delete("/api/chains/{chain_id}")
async def delete_chain(chain_id: str):
    db.delete_chain_run(chain_id)
    return {"ok": True}


# ── Summary / Trends ────────────────────────────────────────

@app.get("/api/summary")
async def get_summary():
    return db.get_summary()


@app.get("/api/latest")
async def get_latest():
    return db.list_results_compact(20)


@app.get("/api/trends")
async def get_trends(
    model: str | None = None,
    preset: str | None = None,
    endpoint: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    group_by: str = "day",
):
    return db.get_trends(model, preset, endpoint, from_date, to_date, group_by)


@app.get("/api/best-worst")
async def get_best_worst():
    return db.get_best_worst()


def main():
    import uvicorn
    # Bind localhost by default: the app has no auth and serves stored API
    # keys, so it must not be exposed to the network unintentionally.
    uvicorn.run("app.main:app", host="127.0.0.1", port=9090)
