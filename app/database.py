from __future__ import annotations

import json
import sqlite3
import os
from contextlib import contextmanager
from typing import List

from .models import EndpointConfig, BenchmarkResult

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "benchmarks.db")


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
        """)
        # Migration: add output_length if it doesn't exist yet
        try:
            conn.execute("ALTER TABLE results ADD COLUMN output_length INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except Exception:
            pass  # column already exists


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


def get_endpoint(ep_id: str) -> Optional[EndpointConfig]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM endpoints WHERE id=?", (ep_id,)).fetchone()
    if row is None:
        return None
    return EndpointConfig(**dict(row))


def delete_endpoint(ep_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM endpoints WHERE id=?", (ep_id,))
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

def list_results_filter(
    limit: int = 200,
    model: str | None = None,
    preset: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> List[BenchmarkResult]:
    conditions = []
    params: list = []
    if model:
        conditions.append("model = ?")
        params.append(model)
    if preset:
        conditions.append("preset_name = ?")
        params.append(preset)
    if from_date:
        conditions.append("created_at >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("created_at <= ?")
        params.append(to_date)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM results{where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [BenchmarkResult(**dict(r)) for r in rows]


# ── Compact listing (no prompt/response body) ──────────────

def list_results_compact(limit: int = 200) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, endpoint_name, model, preset_name,
                   total_time_ms, tokens_per_second, completion_tokens,
                   prompt_tokens, created_at
            FROM results
            ORDER BY created_at DESC LIMIT ?
            """, (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


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
                    AVG(tokens_per_second) as avg_tps
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
