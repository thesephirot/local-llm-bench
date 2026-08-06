from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional


@dataclass
class EndpointConfig:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    extra_headers: str = "{}"  # JSON string


@dataclass
class PromptPreset:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    key: str = ""
    name: str = ""
    prompt: str = ""
    description: str = ""
    steps: list[str] = field(default_factory=list)


@dataclass
class ChainConfig:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    endpoint_id: str = ""
    endpoint_name: str = ""
    # Ordered list of models sharing this config's settings; a chain step
    # is executed per model in this order.
    models: list = field(default_factory=list)
    preset_key: str = ""
    preset_name: str = ""
    max_tokens: int = 2048
    temperature: float = 0.7
    notes: str = ""
    created_at: str = ""


@dataclass
class BenchmarkResult:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    endpoint_id: str = ""
    endpoint_name: str = ""
    model: str = ""
    preset_name: str = ""
    prompt: str = ""
    response: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    time_to_first_token_ms: float = 0.0
    total_time_ms: float = 0.0
    tokens_per_second: float = 0.0
    output_length: int = 0
    created_at: str = ""
    success: bool = True
    error: str = ""
    error_category: str = ""   # one of ErrorCategory values
    status_code: int | None = None
    tokens_estimated: bool = False  # True when token counts fell back to char/4 estimate
    steps: list[dict] = field(default_factory=list)  # per-step {prompt, response, prompt_tokens, completion_tokens, total_time_ms}


@dataclass
class ChainStepResult:
    """Per-step result within a chain run."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    step_index: int = 0
    config_id: str = ""
    config_name: str = ""
    model: str = ""
    benchmark_result: BenchmarkResult | None = None
    error: str = ""
    success: bool = False
    error_category: str = ""   # one of ErrorCategory values
    status_code: int | None = None


@dataclass
class ChainRunResult:
    """Aggregated result for a full chain execution."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    config_ids: list[str] = field(default_factory=list)
    step_results: list[ChainStepResult] = field(default_factory=list)
    total_steps: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    started_at: str = ""
    finished_at: str = ""


class ChainRunRequest(BaseModel):
    """Request body for running a chain of benchmarks."""
    config_ids: list[str]


class ErrorCategory(str, Enum):
    """Structured error classification for benchmark steps."""
    HTTP_ERROR = "http_error"
    TIMEOUT = "timeout"
    NETWORK = "network"
    CANCELLED = "cancelled"
    OTHER = "other"
