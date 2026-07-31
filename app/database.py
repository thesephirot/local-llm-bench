from __future__ import annotations

import asyncio
import datetime
import json
import logging
import sqlite3
import os
import uuid
from contextlib import contextmanager
from typing import List

logger = logging.getLogger(__name__)

from .models import EndpointConfig, PromptPreset, LlamaSwapConfig, BenchmarkResult, ChainRunResult, ChainStepResult

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
                created_at TEXT NOT NULL,
                success INTEGER NOT NULL DEFAULT 1,
                error TEXT NOT NULL DEFAULT '',
                error_category TEXT NOT NULL DEFAULT '',
                status_code INTEGER,
                tokens_estimated INTEGER NOT NULL DEFAULT 0
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
                model TEXT NOT NULL DEFAULT '',
                models TEXT NOT NULL DEFAULT '[]',
                preset_key TEXT NOT NULL DEFAULT '',
                preset_name TEXT NOT NULL DEFAULT '',
                max_tokens INTEGER NOT NULL DEFAULT 2048,
                temperature REAL NOT NULL DEFAULT 0.7,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chain_runs (
                id TEXT PRIMARY KEY,
                config_ids TEXT NOT NULL,
                total_steps INTEGER NOT NULL DEFAULT 0,
                completed_steps INTEGER NOT NULL DEFAULT 0,
                failed_steps INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                current_step_index INTEGER,
                current_model TEXT NOT NULL DEFAULT '',
                steps_done INTEGER NOT NULL DEFAULT 0,
                heartbeat TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS chain_steps (
                id TEXT PRIMARY KEY,
                chain_run_id TEXT NOT NULL,
                step_index INTEGER NOT NULL DEFAULT 0,
                config_id TEXT NOT NULL,
                config_name TEXT NOT NULL,
                model TEXT NOT NULL,
                benchmark_result_id TEXT,
                error TEXT NOT NULL DEFAULT '',
                success INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (chain_run_id) REFERENCES chain_runs(id),
                FOREIGN KEY (benchmark_result_id) REFERENCES results(id)
            );
        """)
        # Migrations: add columns if missing
        for col_def in [
            ("results", "output_length", "INTEGER NOT NULL DEFAULT 0"),
            ("results", "success", "INTEGER NOT NULL DEFAULT 1"),
            ("results", "error", "TEXT NOT NULL DEFAULT ''"),
            ("results", "error_category", "TEXT NOT NULL DEFAULT ''"),
            ("results", "status_code", "INTEGER"),
            ("results", "tokens_estimated", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE {col_def[0]} ADD COLUMN {col_def[1]} {col_def[2]}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists
            except Exception:
                logger.warning("Migration failed for %s.%s", col_def[0], col_def[1], exc_info=True)

        # Chain-related tables are created above; add columns if they were added later
        for col_def in [
            ("chain_steps", "benchmark_result_id", "TEXT"),
            ("chain_steps", "error_category", "TEXT NOT NULL DEFAULT ''"),
            ("chain_steps", "status_code", "INTEGER"),
            ("swap_configs", "models", "TEXT NOT NULL DEFAULT '[]'"),
            ("chain_runs", "current_step_index", "INTEGER"),
            ("chain_runs", "current_model", "TEXT NOT NULL DEFAULT ''"),
            ("chain_runs", "steps_done", "INTEGER NOT NULL DEFAULT 0"),
            ("chain_runs", "heartbeat", "TEXT NOT NULL DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE {col_def[0]} ADD COLUMN {col_def[1]} {col_def[2]}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists
            except Exception:
                logger.warning("Migration failed for %s.%s", col_def[0], col_def[1], exc_info=True)

        # Backfill multi-model list from the legacy single-model column
        for r in conn.execute("SELECT id, model, models FROM swap_configs").fetchall():
            try:
                existing = json.loads(r["models"] or "[]")
            except json.JSONDecodeError:
                existing = []
            if not existing and r["model"]:
                conn.execute("UPDATE swap_configs SET models=? WHERE id=?",
                             (json.dumps([r["model"]]), r["id"]))
        conn.commit()

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


async def _db_sync(func, *args):
    """Run a synchronous DB function in a thread pool to avoid blocking the event loop."""
    return await asyncio.to_thread(func, *args)


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

def _row_to_swap_config(row) -> LlamaSwapConfig:
    """Map a swap_configs row to the dataclass, decoding the models JSON
    and falling back to the legacy single-model column."""
    d = dict(row)
    legacy_model = d.pop("model", "")
    try:
        models = json.loads(d.pop("models", "[]") or "[]")
    except json.JSONDecodeError:
        models = []
    if not models and legacy_model:
        models = [legacy_model]
    d["models"] = models
    return LlamaSwapConfig(**d)


def get_swap_config(cfg_id: str) -> LlamaSwapConfig | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM swap_configs WHERE id=?", (cfg_id,)).fetchone()
    if row is None:
        return None
    return _row_to_swap_config(row)


def list_swap_configs() -> List[LlamaSwapConfig]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM swap_configs ORDER BY created_at DESC").fetchall()
    return [_row_to_swap_config(r) for r in rows]


def save_swap_config(cfg: LlamaSwapConfig) -> LlamaSwapConfig:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO swap_configs (id, name, endpoint_id, endpoint_name, model, models, "
            "preset_key, preset_name, max_tokens, temperature, notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (cfg.id, cfg.name, cfg.endpoint_id, cfg.endpoint_name,
             cfg.models[0] if cfg.models else "", json.dumps(cfg.models),
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
                time_to_first_token_ms, total_time_ms, tokens_per_second, output_length, created_at,
                success, error, error_category, status_code, tokens_estimated
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r.id, r.endpoint_id, r.endpoint_name, r.model, r.preset_name,
             r.prompt, r.response, r.prompt_tokens, r.completion_tokens, r.total_tokens,
             r.time_to_first_token_ms, r.total_time_ms, r.tokens_per_second,
             r.output_length, r.created_at,
             1 if r.success else 0, r.error, r.error_category, r.status_code,
             1 if r.tokens_estimated else 0),
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
                   completion_tokens, output_length, created_at, success, error
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
        failed_runs = conn.execute("SELECT COUNT(*) FROM results WHERE success = 0").fetchone()[0]
        ok_runs = total_runs - failed_runs
        if ok_runs == 0:
            return {
                "total_runs": total_runs,
                "failed_runs": failed_runs,
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
            FROM results WHERE success = 1
            """
        ).fetchone()
        d = dict(row)

        best = conn.execute(
            "SELECT model, tokens_per_second FROM results WHERE success = 1 ORDER BY tokens_per_second DESC LIMIT 1"
        ).fetchone()
        worst = conn.execute(
            "SELECT model, tokens_per_second FROM results WHERE success = 1 ORDER BY tokens_per_second ASC LIMIT 1"
        ).fetchone()

        models_row = conn.execute("SELECT DISTINCT model FROM results").fetchall()
        presets_row = conn.execute("SELECT DISTINCT preset_name FROM results").fetchall()

        model_stats = []
        models = conn.execute("SELECT DISTINCT model FROM results WHERE success = 1").fetchall()
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
                FROM results WHERE model = ? AND success = 1
                """, (model_name,)
            ).fetchone()
            sd = dict(stat)
            sd["model"] = model_name
            model_stats.append(sd)

        return {
            "total_runs": total_runs,
            "failed_runs": failed_runs,
            "unique_models": [r[0] for r in models_row],
            "unique_presets": [r[0] for r in presets_row],
            "avg_latency_ms": round(d["avg_latency_ms"] or 0, 2),
            "avg_ttfb_ms": round(d["avg_ttfb_ms"] or 0, 2),
            "avg_tps": round(d["avg_tps"] or 0, 2),
            "best_tps": round(best[1], 2) if best else 0,
            "best_tps_model": best[0] if best else "",
            "worst_tps": round(worst[1], 2) if worst else 0,
            "worst_tps_model": worst[0] if worst else "",
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
    """Time-series trend data grouped by day/hour (successful runs only)."""
    where, params = _build_filter(model, preset, endpoint, from_date, to_date)
    where = (where + " AND success = 1") if where else " WHERE success = 1"

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
    """Return best and worst runs across key metrics (successful runs only)."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM results WHERE success = 1").fetchone()[0]
        if total == 0:
            return {}

        best_tps = conn.execute(
            "SELECT * FROM results WHERE success = 1 ORDER BY tokens_per_second DESC LIMIT 1"
        ).fetchone()
        worst_tps = conn.execute(
            "SELECT * FROM results WHERE success = 1 ORDER BY tokens_per_second ASC LIMIT 1"
        ).fetchone()
        best_latency = conn.execute(
            "SELECT * FROM results WHERE success = 1 AND total_time_ms > 0 ORDER BY total_time_ms ASC LIMIT 1"
        ).fetchone()
        worst_latency = conn.execute(
            "SELECT * FROM results WHERE success = 1 ORDER BY total_time_ms DESC LIMIT 1"
        ).fetchone()
        best_ttfb = conn.execute(
            "SELECT * FROM results WHERE success = 1 AND time_to_first_token_ms > 0 ORDER BY time_to_first_token_ms ASC LIMIT 1"
        ).fetchone()
        worst_ttfb = conn.execute(
            "SELECT * FROM results WHERE success = 1 ORDER BY time_to_first_token_ms DESC LIMIT 1"
        ).fetchone()

    return {
        "best_tps": dict(best_tps) if best_tps else None,
        "worst_tps": dict(worst_tps) if worst_tps else None,
        "best_latency": dict(best_latency) if best_latency else None,
        "worst_latency": dict(worst_latency) if worst_latency else None,
        "best_ttfb": dict(best_ttfb) if best_ttfb else None,
        "worst_ttfb": dict(worst_ttfb) if worst_ttfb else None,
    }


# ── Chain Runs ─────────────────────────────────────────────

def save_chain_run(cr: ChainRunResult) -> ChainRunResult:
    """Persist a chain run result (without steps). Uses INSERT OR REPLACE for idempotency."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO chain_runs (id, config_ids, total_steps, completed_steps, failed_steps, started_at, finished_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (cr.id, json.dumps(cr.config_ids), cr.total_steps, cr.completed_steps,
             cr.failed_steps, cr.started_at, cr.finished_at),
        )
        conn.commit()
    return cr


_PROGRESS_COLS = ("current_step_index", "current_model", "steps_done", "heartbeat")


def _row_to_chain_run(d: dict) -> ChainRunResult:
    """Build a ChainRunResult from a chain_runs row, stripping the
    live-progress columns (exposed separately via /api/chain-status)."""
    d["config_ids"] = json.loads(d["config_ids"])
    for k in _PROGRESS_COLS:
        d.pop(k, None)
    return ChainRunResult(**d)


def list_chain_runs(limit: int = 100) -> List[ChainRunResult]:
    """List chain runs ordered by most recent."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM chain_runs ORDER BY rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    results = []
    for r in rows:
        cr = _row_to_chain_run(dict(r))
        cr.step_results = list_chain_steps(cr.id)
        results.append(cr)
    return results


def get_chain_run(chain_id: str) -> ChainRunResult | None:
    """Fetch a single chain run with its steps."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM chain_runs WHERE id=?", (chain_id,)).fetchone()
    if row is None:
        return None
    cr = _row_to_chain_run(dict(row))
    cr.step_results = list_chain_steps(chain_id)
    return cr


def update_chain_progress(chain_id: str, step_index: int | None, model: str, steps_done: int) -> None:
    """Heartbeat used by /api/chain-status to survive server restarts."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE chain_runs SET current_step_index=?, current_model=?, steps_done=?, heartbeat=? WHERE id=?",
            (step_index, model, steps_done, datetime.datetime.now().isoformat(), chain_id),
        )
        conn.commit()


def list_unfinished_chains() -> List[dict]:
    """Chains never finalized (running, or orphaned by a crash/restart)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, started_at, total_steps, current_step_index, current_model, steps_done, heartbeat "
            "FROM chain_runs WHERE finished_at='' ORDER BY rowid DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def save_chain_step(cs: ChainStepResult, chain_run_id: str) -> ChainStepResult:
    """Persist a single step within a chain run."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chain_steps (id, chain_run_id, step_index, config_id, config_name, model, benchmark_result_id, error, success, error_category, status_code) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cs.id if hasattr(cs, 'id') and cs.id else uuid.uuid4().hex[:12],
             chain_run_id, cs.step_index, cs.config_id, cs.config_name,
             cs.model, cs.benchmark_result.id if cs.benchmark_result else None,
             cs.error, 1 if cs.success else 0,
             getattr(cs, 'error_category', ''), getattr(cs, 'status_code', None)),
        )
        conn.commit()
    return cs


def list_chain_steps(chain_run_id: str) -> List[ChainStepResult]:
    """Fetch all steps for a chain run in order."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM chain_steps WHERE chain_run_id=? ORDER BY step_index ASC",
            (chain_run_id,),
        ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        cs = ChainStepResult(
            step_index=d["step_index"],
            config_id=d["config_id"],
            config_name=d["config_name"],
            model=d["model"],
            error=d["error"],
            success=bool(d["success"]),
            error_category=d.get("error_category", ""),
            status_code=d.get("status_code"),
        )
        # Attach benchmark_result if available
        if d.get("benchmark_result_id"):
            br = get_result(d["benchmark_result_id"])
            if br:
                cs.benchmark_result = BenchmarkResult(**br)
        results.append(cs)
    return results


def delete_chain_run(chain_id: str) -> None:
    """Delete a chain run and all its steps."""
    with get_conn() as conn:
        conn.execute("DELETE FROM chain_steps WHERE chain_run_id=?", (chain_id,))
        conn.execute("DELETE FROM chain_runs WHERE id=?", (chain_id,))
        conn.commit()
