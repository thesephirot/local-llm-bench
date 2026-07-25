from __future__ import annotations

import json
import time
import datetime
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .models import EndpointConfig, PromptPreset, LlamaSwapConfig, BenchmarkResult
from . import database as db

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
    max_tokens: int = 2048
    temperature: float = 0.7


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

    headers = {"Authorization": f"Bearer {ep.api_key}", "Content-Type": "application/json"}
    try:
        extra = json.loads(ep.extra_headers)
        headers.update(extra)
    except json.JSONDecodeError:
        pass

    result = BenchmarkResult(
        endpoint_id=data.endpoint_id,
        endpoint_name=ep.name,
        model=data.model,
        preset_name=preset["name"],
        prompt=preset["prompt"],
    )

    payload = {
        "model": data.model,
        "messages": [{"role": "user", "content": preset["prompt"]}],
        "max_tokens": data.max_tokens,
        "temperature": data.temperature,
        "stream": True,
    }

    async with httpx.AsyncClient(timeout=120) as client:
        start = time.monotonic()
        first_token_time = None
        full_response = []
        try:
            async with client.stream("POST", f"{ep.base_url}/v1/chat/completions",
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
            db.save_result(result)
            return result

        result.response = "".join(full_response)
        result.output_length = len(result.response)
        result.total_time_ms = (time.monotonic() - start) * 1000
        result.time_to_first_token_ms = first_token_time or 0

        # Fallback token estimation when the API doesn't return usage.
        # Rough heuristic: ~4 chars per token for English text. Mark as estimated.
        if result.completion_tokens == 0:
            result.completion_tokens = max(1, len(result.response) // 4)
        if result.prompt_tokens == 0:
            result.prompt_tokens = max(1, len(preset["prompt"]) // 4)
        result.total_tokens = result.prompt_tokens + result.completion_tokens

        # tok/s = generation speed, so subtract prompt processing time (TTFT)
        generation_ms = result.total_time_ms - (result.time_to_first_token_ms or 0)
        if generation_ms > 0:
            result.tokens_per_second = result.completion_tokens / (generation_ms / 1000)
        elif result.total_time_ms > 0:
            result.tokens_per_second = result.completion_tokens / (result.total_time_ms / 1000)
        result.created_at = datetime.datetime.now().isoformat()

    db.save_result(result)
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
