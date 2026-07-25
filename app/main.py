from __future__ import annotations

import json
import time
import datetime
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .models import EndpointConfig, BenchmarkResult
from . import database as db

db.init_db()

app = FastAPI(title="LLM Benchmark Dashboard")

# Serve static frontend
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── Preset benchmarks ──────────────────────────────────────

PRESETS = {
    "simple": {
        "name": "Simple Echo",
        "prompt": "Hello, how are you today?",
        "description": "Short conversational prompt — measures basic latency.",
    },
    "code": {
        "name": "Code Generation",
        "prompt": (
            "Write a Python function that computes the Fibonacci sequence up to n terms "
            "using an iterative approach. Include docstrings and type hints."
        ),
        "description": "Code generation — tests structured output.",
    },
    "reasoning": {
        "name": "Logical Reasoning",
        "prompt": (
            "A farmer has 17 sheep. All but 9 run away. How many sheep does the farmer have left? "
            "Explain your reasoning step by step."
        ),
        "description": "Trick question — tests reasoning over pattern-matching.",
    },
    "long": {
        "name": "Long Response",
        "prompt": (
            "Write a comprehensive essay about the history and impact of the internet, "
            "covering its origins in the 1960s, the rise of the World Wide Web, "
            "the dot-com bubble, social media, and the modern era. Include key dates "
            "and figures."
        ),
        "description": "Long-form generation — tests sustained throughput.",
    },
    "translation": {
        "name": "Translation",
        "prompt": (
            'Translate the following English paragraph into French, then back into English: "The quick brown fox jumps over the lazy dog." '
            "Show each step."
        ),
        "description": "Round-trip translation — tests multilingual ability.",
    },
}


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


# ── API Routes ──────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/api/presets")
async def get_presets():
    return PRESETS


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


@app.post("/api/run")
async def run_benchmark(data: BenchmarkRun):
    ep = db.get_endpoint(data.endpoint_id)
    if not ep:
        raise HTTPException(404, "Endpoint not found")

    preset = PRESETS.get(data.preset)
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
                    # SSE format: lines starting with "data: "
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

                            # Usage summary may come in last chunk
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

        # Fallback token counting (estimate from response length)
        if result.completion_tokens == 0:
            result.completion_tokens = len(result.response) // 4
        if result.prompt_tokens == 0:
            result.prompt_tokens = len(preset["prompt"]) // 4
        result.total_tokens = result.prompt_tokens + result.completion_tokens

        if result.total_time_ms > 0:
            result.tokens_per_second = result.completion_tokens / (result.total_time_ms / 1000)
        result.created_at = datetime.datetime.now().isoformat()

    db.save_result(result)
    return result


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
    from_date: str | None = None,
    to_date: str | None = None,
):
    """Compact listing for the history table — no prompt/response bodies."""
    return db.list_results_history(limit, model, preset, from_date, to_date)


@app.get("/api/results/{result_id}")
async def get_result(result_id: str):
    """Fetch a single result with full details."""
    result = db.get_result(result_id)
    if not result:
        raise HTTPException(404, "Result not found")
    return result


@app.get("/api/summary")
async def get_summary():
    return db.get_summary()


@app.get("/api/latest")
async def get_latest():
    return db.list_results_compact(20)


@app.delete("/api/results/{result_id}")
async def delete_result(result_id: str):
    db.delete_result(result_id)
    return {"ok": True}


@app.delete("/api/results")
async def delete_all_results():
    db.delete_all_results()
    return {"ok": True}
