"""Shared pytest fixtures for FastAPI endpoint tests."""
import os
import sys
import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

# Ensure the project root is on sys.path so `app` is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import database as db  # noqa: E402


@pytest.fixture
def _db_path():
    """Create a fresh temporary SQLite DB for each test."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig = db.DB_PATH
    db.DB_PATH = tmp.name
    db.init_db()
    yield tmp.name
    db.DB_PATH = orig
    if os.path.exists(tmp.name):
        os.unlink(tmp.name)


@pytest.fixture()
def client(_db_path):
    """Return a TestClient pointing at the FastAPI app with a fresh DB."""
    from app.main import app  # noqa: E402
    with TestClient(app) as c:
        yield c
