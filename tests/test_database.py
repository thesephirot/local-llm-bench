"""Tests for database layer — chain run CRUD."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import database as db
from app.models import ChainRunResult, ChainStepResult


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Point the database layer at an isolated temporary DB (auto-restored)."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    return db


def _make_run(**overrides):
    defaults = dict(
        config_ids=["cfg1"],
        total_steps=1,
        completed_steps=0,
        failed_steps=0,
        started_at="2025-01-01T00:00:00",
        finished_at="2025-01-01T00:01:00",
    )
    defaults.update(overrides)
    return ChainRunResult(**defaults)


def test_save_and_get_chain_run(fresh_db):
    cr = _make_run(config_ids=["cfg1", "cfg2"], total_steps=2, completed_steps=1, failed_steps=1)
    saved = db.save_chain_run(cr)
    assert saved.id == cr.id

    fetched = db.get_chain_run(cr.id)
    assert fetched is not None
    assert fetched.total_steps == 2
    assert fetched.completed_steps == 1
    assert len(fetched.step_results) == 0  # no steps yet


def test_save_and_list_chain_steps(fresh_db):
    cr = _make_run()
    db.save_chain_run(cr)

    cs = ChainStepResult(
        step_index=0,
        config_id="cfg1",
        config_name="Test Config",
        model="test-model",
        success=False,
        error="placeholder",
    )
    db.save_chain_step(cs, cr.id)

    steps = db.list_chain_steps(cr.id)
    assert len(steps) == 1
    assert steps[0].step_index == 0
    assert steps[0].config_name == "Test Config"


def test_delete_chain_run(fresh_db):
    cr = _make_run()
    db.save_chain_run(cr)

    cs = ChainStepResult(step_index=0, config_id="cfg1", config_name="C", model="m")
    db.save_chain_step(cs, cr.id)

    db.delete_chain_run(cr.id)
    assert db.get_chain_run(cr.id) is None
    assert db.list_chain_steps(cr.id) == []


def test_list_chain_runs(fresh_db):
    for i in range(3):
        db.save_chain_run(_make_run(config_ids=[f"cfg{i}"]))

    runs = db.list_chain_runs(limit=10)
    assert len(runs) == 3
