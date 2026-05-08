"""Smoke test for scripts/wait_for_database.py (no PostgreSQL client binary required)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


_BACKEND_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.timeout(30)
def test_wait_for_database_sqlite_success(tmp_path) -> None:
    dbf = tmp_path / "wait.sqlite3"
    url = f"sqlite+pysqlite:///{dbf}"
    env = {**os.environ, "DATABASE_URL": url, "DEVNEST_DB_WAIT_TIMEOUT_SECONDS": "15"}
    script = _BACKEND_ROOT / "scripts" / "wait_for_database.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(_BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout


@pytest.mark.timeout(30)
def test_wait_for_database_missing_url_fails() -> None:
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["DEVNEST_DB_WAIT_TIMEOUT_SECONDS"] = "1"
    script = _BACKEND_ROOT / "scripts" / "wait_for_database.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(_BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    assert proc.returncode == 2
