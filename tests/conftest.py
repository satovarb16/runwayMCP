"""Shared pytest fixtures for the runway-mcp test suite.

db_path monkeypatches tools._db._DB_PATH to a temp location, mirroring the
_X_PATH monkeypatch convention used across the JSON-era test suite
(_JOBS_PATH, _RESUMES_PATH). All four SQLite-era test files (test_db.py,
test_migration.py, test_jobs_store.py, test_resumes.py) share this fixture
rather than each hand-rolling their own patch helper.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def db_path(monkeypatch, tmp_path: Path) -> Path:
    """Redirect tools._db._DB_PATH to a temp location for test isolation."""
    import tools._db as db_mod

    new_path = tmp_path / "runway.db"
    monkeypatch.setattr(db_mod, "_DB_PATH", new_path)
    return new_path
