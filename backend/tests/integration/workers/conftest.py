"""Fixtures for worker integration tests (PostgreSQL from ``tests/integration/conftest.py``)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def patch_worker_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic monotonic UTC timestamps for ``worker._now`` (matches unit worker tests)."""
    base = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    state = {"n": 0}

    def _tick() -> datetime:
        i = state["n"]
        state["n"] = i + 1
        return base + timedelta(seconds=i)

    import app.workers.workspace_job_worker.worker as worker_mod

    monkeypatch.setattr(worker_mod, "_now", _tick)
