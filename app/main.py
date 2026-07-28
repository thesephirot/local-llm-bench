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


# ── SSE Streaming for Chain Progress ───────────────────────

async def _stream_chain_execution(config_ids: list[str]):
    """Execute chain steps sequentially and yield SSE events as each completes."""

    # Resolve configs and build ordered list of steps
    steps: list[ChainStepResult] = []
    for idx, cfg_id in enumerate(config_ids):
        cfg = db.get_swap_config(cfg_id)
        if not cfg:
            step = ChainStepResult(step_index=idx, config_id=cfg_id,
                                   error=f"Config {cfg_id} not found", success=False,
                                   error_category=ErrorCategory.OTHER.value)
            steps.append(step)
            continue
        step = ChainStepResult(step_index=idx, config_id=cfg.id,
                               config_name=cfg.name, model=cfg.model)
        steps.append(step)

    # Create and persist the chain run record
    chain_result = ChainRunResult(
        config_ids=config_ids,
        step_results=[],
        total_steps=len(steps),
        started_at=datetime.datetime.now().isoformat(),
    )
    db.save_chain_run(chain_result)

    yield f'event: start\ndata: {{"chain_id": "{json.dumps(chain_result.id)}"}}\n\n'

    # Resolve presets once
    presets = db.presets_as_dict()

    completed = 0
    failed = 0

    for step in steps:
        cfg = db.get_swap_config(step.config_id)
        if not cfg or not cfg.endpoint_id:
            step.error = f"Config {step.config_id} not found"
            step.success = False
            step.error_category = ErrorCategory.OTHER.value
            failed += 1
            db.save_chain_step(step, chain_result.id)
            yield _sse_step_event(step)
            continue

        ep = await _db_sync(db.get_endpoint, cfg.endpoint_id)
        if not ep:
            step.error = "Endpoint not found"
            step.success = False
            step.error_category = ErrorCategory.OTHER.value
            failed += 1
            await _db_sync(db.save_chain_step, step, chain_result.id)
            yield _sse_step_event(step)
            continue

        preset_name = cfg.preset_key or "simple"
        preset = presets.get(preset_name, {})
        preset_prompt = preset.get("prompt", "Hello, how are you today?")
        preset_display = preset.get("name", preset_name)

        try:
            br = await _run_single_benchmark(
                endpoint=ep,
                model=cfg.model,
                preset_name=preset_display,
                preset_prompt=preset_prompt,
                max_tokens=cfg.max_tokens,
                temperature=cfg.temperature,
            )
            step.benchmark_result = br
            step.success = True
            completed += 1
        except httpx.TimeoutException as e:
            step.error = str(e)
            step.error_category = ErrorCategory.TIMEOUT.value
            step.success = False
            failed += 1
        except (httpx.ConnectError, httpx.NetworkError) as e:
            step.error = str(e)
            step.error_category = ErrorCategory.NETWORK.value
            step.success = False
            failed += 1
        except httpx.HTTPStatusError as e:
            step.error = f"HTTP {e.response.status_code}: {e.response.text}"
            step.error_category = ErrorCategory.HTTP_ERROR.value
            step.status_code = e.response.status_code
            step.success = False
            failed += 1
        except Exception as e:
            step.error = str(e)
            step.error_category = ErrorCategory.OTHER.value
            step.success = False
            failed += 1

        await _db_sync(db.save_chain_step, step, chain_result.id)
        yield _sse_step_event(step)

    finished = datetime.datetime.now().isoformat()
    chain_result.finished_at = finished
    chain_result.completed_steps = completed
    chain_result.failed_steps = failed
    await _db_sync(db.save_chain_run, chain_result)

    yield f"event: complete\ndata: {{\"completed_steps\": {completed}, \"failed_steps\": {failed}}}\n\n"


def _sse_step_event(step: ChainStepResult) -> str:
    """Format a single step result as an SSE event string."""
    data = {
        "step_index": step.step_index,
        "config_name": step.config_name,
        "model": step.model,
        "success": step.success,
        "error": step.error,
        "error_category": step.error_category,
        "status_code": step.status_code,
    }
    if step.benchmark_result:
        br = step.benchmark_result
        data["benchmark_result"] = {
            "tokens_per_second": br.tokens_per_second,
            "total_time_ms": br.total_time_ms,
            "completion_tokens": br.completion_tokens,
            "prompt_tokens": br.prompt_tokens,
        }
    return f"event: step\ndata: {json.dumps(data)}\n\n"


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
    headers = {"Authorization": f"Bearer {endpoint.api_key}", "Content-Type": "application/json"}
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
    }

    async with httpx.AsyncClient(timeout=120) as client:
        start = time.monotonic()
        first_token_time = None
        full_response = []
        try:
            async with client.stream("POST", f"{endpoint.base_url}/v1/chat/completions",
                                     json=payload, headers=headers) as resp:
                resp.raise_for_status()
                async for chunk_bytes in resp.aiter_bytes():
                    text = chunk_bytes.decode().strip()
                    if not text:
                        continue
                    for line in text.split("\n"):
                        if not line.startswith("data: "):
                            continue
                        json_str = line[6:]
                        if json_str == "[DONE]":
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
                                result.prompt_tokens = usage.get("prompt_tokens", 0)
                                result.completion_tokens = usage.get("completion_tokens", 0)
                                result.total_tokens = usage.get("total_tokens", 0)
                        except json.JSONDecodeError:
                            continue
        except httpx.HTTPStatusError as e:
            result.response = f"HTTP {e.response.status_code}: {e.response.text}"
            result.total_time_ms = (time.monotonic() - start) * 1000
            result.prompt_tokens = 0
            result.completion_tokens = 0
            result.total_tokens = 0
            result.tokens_per_second = 0.0
            result.output_length = len(result.response)
            result.created_at = datetime.datetime.now().isoformat()
            db.save_result(result)
            return result

        result.response = "".join(full_response)
        result.output_length = len(result.response)
        result.total_time_ms = (time.monotonic() - start) * 1000
        result.time_to_first_token_ms = first_token_time or 0

        # Fallback token estimation when the API doesn't return usage.
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

    db.save_result(result)
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

    headers = {"Authorization": f"Bearer {ep.api_key}"}
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

    # Resolve configs and build ordered list of steps
    steps: list[ChainStepResult] = []
    for idx, cfg_id in enumerate(data.config_ids):
        cfg = await _db_sync(db.get_swap_config, cfg_id)
        if not cfg:
            step = ChainStepResult(step_index=idx, config_id=cfg_id,
                                   error=f"Config {cfg_id} not found", success=False,
                                   error_category=ErrorCategory.OTHER.value)
            steps.append(step)
            continue
        step = ChainStepResult(step_index=idx, config_id=cfg.id,
                               config_name=cfg.name, model=cfg.model)
        steps.append(step)

    # Create and persist the chain run record
    chain_result = ChainRunResult(
        config_ids=data.config_ids,
        step_results=[],  # populated below
        total_steps=len(steps),
        started_at=datetime.datetime.now().isoformat(),
    )
    await _db_sync(db.save_chain_run, chain_result)

    # Resolve presets once for all steps
    presets = db.presets_as_dict()

    # Execute each step sequentially, reusing the shared benchmark logic
    completed = 0
    failed = 0
    for step in steps:
        cfg = await _db_sync(db.get_swap_config, step.config_id)
        if not cfg or not cfg.endpoint_id:
            step.error = f"Config {step.config_id} not found"
            step.success = False
            step.error_category = ErrorCategory.OTHER.value
            failed += 1
            await _db_sync(db.save_chain_step, step, chain_result.id)
            continue

        ep = await _db_sync(db.get_endpoint, cfg.endpoint_id)
        if not ep:
            step.error = "Endpoint not found"
            step.success = False
            step.error_category = ErrorCategory.OTHER.value
            failed += 1
            await _db_sync(db.save_chain_step, step, chain_result.id)
            continue

        preset_name = cfg.preset_key or "simple"
        preset = presets.get(preset_name, {})
        preset_prompt = preset.get("prompt", "Hello, how are you today?")
        preset_display = preset.get("name", preset_name)

        try:
            br = await _run_single_benchmark(
                endpoint=ep,
                model=cfg.model,
                preset_name=preset_display,
                preset_prompt=preset_prompt,
                max_tokens=cfg.max_tokens,
                temperature=cfg.temperature,
            )
            step.benchmark_result = br
            step.success = True
            completed += 1
        except httpx.TimeoutException as e:
            step.error = str(e)
            step.error_category = ErrorCategory.TIMEOUT.value
            step.success = False
            failed += 1
        except (httpx.ConnectError, httpx.NetworkError) as e:
            step.error = str(e)
            step.error_category = ErrorCategory.NETWORK.value
            step.success = False
            failed += 1
        except httpx.HTTPStatusError as e:
            step.error = f"HTTP {e.response.status_code}: {e.response.text}"
            step.error_category = ErrorCategory.HTTP_ERROR.value
            step.status_code = e.response.status_code
            step.success = False
            failed += 1
        except Exception as e:
            step.error = str(e)
            step.error_category = ErrorCategory.OTHER.value
            step.success = False
            failed += 1

        await _db_sync(db.save_chain_step, step, chain_result.id)

    finished = datetime.datetime.now().isoformat()
    chain_result.finished_at = finished
    chain_result.completed_steps = completed
    chain_result.failed_steps = failed
    # Update the persisted chain run with final counts
    await _db_sync(db.save_chain_run, chain_result)

    return {
        "id": chain_result.id,
        "total_steps": chain_result.total_steps,
        "completed_steps": chain_result.completed_steps,
        "failed_steps": chain_result.failed_steps,
        "started_at": chain_result.started_at,
        "finished_at": finished,
        "steps": [
            {
                "step_index": s.step_index,
                "config_id": s.config_id,
                "config_name": s.config_name,
                "model": s.model,
                "success": s.success,
                "error": s.error,
                "error_category": getattr(s, "error_category", None),
                "status_code": getattr(s, "status_code", None),
                "benchmark_result": (
                    {"id": s.benchmark_result.id, "tokens_per_second": s.benchmark_result.tokens_per_second,
                     "total_time_ms": s.benchmark_result.total_time_ms, "completion_tokens": s.benchmark_result.completion_tokens}
                    if s.benchmark_result else None
                ),
            }
            for s in steps
        ],
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
    uvicorn.run("app.main:app", host="0.0.0.0", port=9090, reload=True)
