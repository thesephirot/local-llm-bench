from __future__ import annotations

import json
import sqlite3
import os
from contextlib import contextmanager
from typing import List

from .models import EndpointConfig, PromptPreset, LlamaSwapConfig, BenchmarkResult

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "benchmarks.db")

# Seed presets when no user presets exist
SEED_PRESETS = [
    {"key": "simple", "name": "Simple Echo", "description": "Short conversational prompt — measures basic latency.",
     "prompt": "Hello, how are you today?"},
    {"key": "code", "name": "Code Generation", "description": "Code generation — tests structured output.",
     "prompt": "Write a Python function that computes the Fibonacci sequence up to n terms using an iterative approach. Include docstrings and type hints."},
    {"key": "reasoning", "name": "Logical Reasoning", "description": "Trick question — tests reasoning over pattern-matching.",
     "prompt": "A farmer has 17 sheep. All but 9 run away. How many sheep does the farmer have left? Explain your reasoning step by step."},
    {"key": "long", "name": "Long Response", "description": "Long-form generation — tests sustained throughput.",
     "prompt": "Write a comprehensive essay about the history and impact of the internet, covering its origins in the 1960s, the rise of the World Wide Web, the dot-com bubble, social media, and the modern era. Include key dates and figures."},
    {"key": "translation", "name": "Translation", "description": "Round-trip translation — tests multilingual ability.",
     "prompt": 'Translate the following English paragraph into French, then back into English: "The quick brown fox jumps over the lazy dog." Show each step.'},
]


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS endpoints (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT NOT NULL DEFAULT '',
                extra_headers TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS results (
                id TEXT PRIMARY KEY,
                endpoint_id TEXT NOT NULL,
                endpoint_name TEXT NOT NULL,
                model TEXT NOT NULL,
                preset_name TEXT NOT NULL,
                prompt TEXT NOT NULL,
                response TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                time_to_first_token_ms REAL NOT NULL DEFAULT 0,
                total_time_ms REAL NOT NULL DEFAULT 0,
                tokens_per_second REAL NOT NULL DEFAULT 0,
                output_length INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS presets (
                id TEXT PRIMARY KEY,
                key TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                prompt TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS swap_configs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                endpoint_id TEXT NOT NULL,
                endpoint_name TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL,
                preset_key TEXT NOT NULL DEFAULT '',
                preset_name TEXT NOT NULL DEFAULT '',
                max_tokens INTEGER NOT NULL DEFAULT 2048,
                temperature REAL NOT NULL DEFAULT 0.7,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
        """)
        # Migrations: add columns if missing
        for col_def in [
            ("results", "output_length", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE {col_def[0]} ADD COLUMN {col_def[1]} {col_def[2]}")
                conn.commit()
            except Exception:
                pass

        # Seed presets if table is empty
        row = conn.execute("SELECT COUNT(*) FROM presets").fetchone()
        if row[0] == 0:
            for p in SEED_PRESETS:
                preset = PromptPreset(key=p["key"], name=p["name"], prompt=p["prompt"], description=p["description"])
                conn.execute(
                    "INSERT INTO presets (id, key, name, prompt, description) VALUES (?,?,?,?,?)",
                    (preset.id, preset.key, preset.name, preset.prompt, preset.description),
                )
            conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ── Endpoints ──────────────────────────────────────────────

def save_endpoint(ep: EndpointConfig) -> EndpointConfig:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO endpoints (id, name, base_url, api_key, extra_headers) VALUES (?,?,?,?,?)",
            (ep.id, ep.name, ep.base_url, ep.api_key, ep.extra_headers),
        )
        conn.commit()
    return ep


def list_endpoints() -> List[EndpointConfig]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM endpoints ORDER BY rowid DESC").fetchall()
    return [EndpointConfig(**dict(r)) for r in rows]


def get_endpoint(ep_id: str) -> EndpointConfig | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM endpoints WHERE id=?", (ep_id,)).fetchone()
    if row is None:
        return None
    return EndpointConfig(**dict(row))


def delete_endpoint(ep_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM endpoints WHERE id=?", (ep_id,))
        conn.commit()


# ── Presets ────────────────────────────────────────────────

def list_presets() -> List[PromptPreset]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM presets ORDER BY rowid ASC").fetchall()
    return [PromptPreset(**dict(r)) for r in rows]


def save_preset(p: PromptPreset) -> PromptPreset:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO presets (id, key, name, prompt, description) VALUES (?,?,?,?,?)",
            (p.id, p.key, p.name, p.prompt, p.description),
        )
        conn.commit()
    return p


def delete_preset(preset_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM presets WHERE id=?", (preset_id,))
        conn.commit()


def presets_as_dict() -> dict:
    """Return presets keyed by their key field (for backward compat)."""
    result = {}
    for p in list_presets():
        result[p.key] = {
            "id": p.id,
            "key": p.key,
            "name": p.name,
            "prompt": p.prompt,
            "description": p.description,
        }
    return result


# ── Swap Configs ───────────────────────────────────────────

def list_swap_configs() -> List[LlamaSwapConfig]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM swap_configs ORDER BY created_at DESC").fetchall()
    return [LlamaSwapConfig(**dict(r)) for r in rows]


def save_swap_config(cfg: LlamaSwapConfig) -> LlamaSwapConfig:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO swap_configs (id, name, endpoint_id, endpoint_name, model, "
            "preset_key, preset_name, max_tokens, temperature, notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cfg.id, cfg.name, cfg.endpoint_id, cfg.endpoint_name, cfg.model,
             cfg.preset_key, cfg.preset_name, cfg.max_tokens, cfg.temperature,
             cfg.notes, cfg.created_at),
        )
        conn.commit()
    return cfg


def delete_swap_config(cfg_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM swap_configs WHERE id=?", (cfg_id,))
        conn.commit()


# ── Results ────────────────────────────────────────────────

def save_result(r: BenchmarkResult) -> BenchmarkResult:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO results (
                id, endpoint_id, endpoint_name, model, preset_name,
                prompt, response, prompt_tokens, completion_tokens, total_tokens,
                time_to_first_token_ms, total_time_ms, tokens_per_second, output_length, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r.id, r.endpoint_id, r.endpoint_name, r.model, r.preset_name,
             r.prompt, r.response, r.prompt_tokens, r.completion_tokens, r.total_tokens,
             r.time_to_first_token_ms, r.total_time_ms, r.tokens_per_second,
             r.output_length, r.created_at),
        )
        conn.commit()
    return r


def list_results(limit: int = 200) -> List[BenchmarkResult]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM results ORDER BY rowid DESC LIMIT ?", (limit,)
        ).fetchall()
    return [BenchmarkResult(**dict(r)) for r in rows]


def delete_result(result_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM results WHERE id=?", (result_id,))
        conn.commit()


# ── Filtered listing ───────────────────────────────────────

def _build_filter(
    model: str | None = None,
    preset: str | None = None,
    endpoint: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> tuple[str, list]:
    conditions = []
    params: list = []
    if model:
        conditions.append("model = ?")
        params.append(model)
    if preset:
        conditions.append("preset_name = ?")
        params.append(preset)
    if endpoint:
        conditions.append("endpoint_id = ?")
        params.append(endpoint)
    if from_date:
        conditions.append("created_at >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("created_at <= ?")
        params.append(to_date)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params


def list_results_filter(
    limit: int = 200,
    model: str | None = None,
    preset: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> List[BenchmarkResult]:
    where, params = _build_filter(model, preset, from_date=from_date, to_date=to_date)
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM results{where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [BenchmarkResult(**dict(r)) for r in rows]


def list_results_compact(limit: int = 200) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, endpoint_name, model, preset_name,
                   total_time_ms, tokens_per_second, completion_tokens,
                   prompt_tokens, output_length, created_at
            FROM results
            ORDER BY created_at DESC LIMIT ?
            """, (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def list_results_history(
    limit: int = 200,
    model: str | None = None,
    preset: str | None = None,
    endpoint: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict]:
    """Compact listing for the history table — no prompt/response bodies."""
    where, params = _build_filter(model, preset, endpoint, from_date, to_date)
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT id, endpoint_id, endpoint_name, model, preset_name,
                   total_time_ms, time_to_first_token_ms, tokens_per_second,
                   completion_tokens, output_length, created_at
            FROM results{where}
            ORDER BY created_at DESC LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_result(result_id: str) -> dict | None:
    """Fetch a single result with full prompt/response."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM results WHERE id = ?", (result_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


# ── Comparison ─────────────────────────────────────────────

def compare_results(result_ids: list[str]) -> list[dict]:
    """Fetch full results for comparison."""
    if not result_ids:
        return []
    results = []
    for rid in result_ids:
        r = get_result(rid)
        if r:
            results.append(r)
    return results


# ── Summary statistics ─────────────────────────────────────

def get_summary() -> dict:
    with get_conn() as conn:
        total_runs = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        if total_runs == 0:
            return {
                "total_runs": 0,
                "unique_models": [],
                "unique_presets": [],
                "avg_latency_ms": 0,
                "avg_ttfb_ms": 0,
                "avg_tps": 0,
                "best_tps": 0,
                "best_tps_model": "",
                "worst_tps": 0,
                "worst_tps_model": "",
                "model_stats": [],
            }

        row = conn.execute(
            """
            SELECT
                AVG(total_time_ms) as avg_latency_ms,
                AVG(time_to_first_token_ms) as avg_ttfb_ms,
                AVG(tokens_per_second) as avg_tps
            FROM results
            """
        ).fetchone()
        d = dict(row)

        best = conn.execute(
            "SELECT model, tokens_per_second FROM results ORDER BY tokens_per_second DESC LIMIT 1"
        ).fetchone()
        worst = conn.execute(
            "SELECT model, tokens_per_second FROM results ORDER BY tokens_per_second ASC LIMIT 1"
        ).fetchone()

        models_row = conn.execute("SELECT DISTINCT model FROM results").fetchall()
        presets_row = conn.execute("SELECT DISTINCT preset_name FROM results").fetchall()

        model_stats = []
        models = conn.execute("SELECT DISTINCT model FROM results").fetchall()
        for m in models:
            model_name = m[0]
            stat = conn.execute(
                """
                SELECT
                    COUNT(*) as count,
                    AVG(total_time_ms) as avg_latency_ms,
                    AVG(time_to_first_token_ms) as avg_ttfb_ms,
                    AVG(tokens_per_second) as avg_tps,
                    MIN(total_time_ms) as min_latency,
                    MAX(total_time_ms) as max_latency
                FROM results WHERE model = ?
                """, (model_name,)
            ).fetchone()
            sd = dict(stat)
            sd["model"] = model_name
            model_stats.append(sd)

        return {
            "total_runs": total_runs,
            "unique_models": [r[0] for r in models_row],
            "unique_presets": [r[0] for r in presets_row],
            "avg_latency_ms": round(d["avg_latency_ms"], 2),
            "avg_ttfb_ms": round(d["avg_ttfb_ms"], 2),
            "avg_tps": round(d["avg_tps"], 2),
            "best_tps": round(best[1], 2),
            "best_tps_model": best[0],
            "worst_tps": round(worst[1], 2),
            "worst_tps_model": worst[0],
            "model_stats": model_stats,
        }


def delete_all_results() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM results")
        conn.commit()


# ── Trends ─────────────────────────────────────────────────

def get_trends(
    model: str | None = None,
    preset: str | None = None,
    endpoint: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    group_by: str = "day",
) -> list[dict]:
    """Time-series trend data grouped by day/hour."""
    where, params = _build_filter(model, preset, endpoint, from_date, to_date)

    if group_by == "hour":
        date_fmt = "strftime('%Y-%m-%d %H:00', created_at)"
    else:
        date_fmt = "strftime('%Y-%m-%d', created_at)"

    query = f"""
        SELECT
            {date_fmt} as period,
            COUNT(*) as count,
            AVG(total_time_ms) as avg_latency_ms,
            AVG(time_to_first_token_ms) as avg_ttfb_ms,
            AVG(tokens_per_second) as avg_tps,
            AVG(completion_tokens) as avg_tokens
        FROM results{where}
        GROUP BY period
        ORDER BY period ASC
    """
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_best_worst() -> dict:
    """Return best and worst runs across key metrics."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        if total == 0:
            return {}

        best_tps = conn.execute(
            "SELECT * FROM results ORDER BY tokens_per_second DESC LIMIT 1"
        ).fetchone()
        worst_tps = conn.execute(
            "SELECT * FROM results ORDER BY tokens_per_second ASC LIMIT 1"
        ).fetchone()
        best_latency = conn.execute(
            "SELECT * FROM results WHERE total_time_ms > 0 ORDER BY total_time_ms ASC LIMIT 1"
        ).fetchone()
        worst_latency = conn.execute(
            "SELECT * FROM results ORDER BY total_time_ms DESC LIMIT 1"
        ).fetchone()
        best_ttfb = conn.execute(
            "SELECT * FROM results WHERE time_to_first_token_ms > 0 ORDER BY time_to_first_token_ms ASC LIMIT 1"
        ).fetchone()
        worst_ttfb = conn.execute(
            "SELECT * FROM results ORDER BY time_to_first_token_ms DESC LIMIT 1"
        ).fetchone()

    return {
        "best_tps": dict(best_tps) if best_tps else None,
        "worst_tps": dict(worst_tps) if worst_tps else None,
        "best_latency": dict(best_latency) if best_latency else None,
        "worst_latency": dict(worst_latency) if worst_latency else None,
        "best_ttfb": dict(best_ttfb) if best_ttfb else None,
        "worst_ttfb": dict(worst_ttfb) if worst_ttfb else None,
    }
