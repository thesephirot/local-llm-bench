from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class EndpointConfig:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    extra_headers: str = "{}"  # JSON string


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
