"""Fixtures for snapshot integration tests.

Pytest 8+ disallows nested ``pytest_plugins`` to import sibling packages, so we duplicate the small
``patch_worker_now`` helper from ``tests/integration/workers/conftest.py`` instead of pulling it in
via plugins. PostgreSQL and ``db_session`` still come from ``tests/integration/conftest.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def patch_worker_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic monotonic UTC timestamps for ``worker._now``."""
    base = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    state = {"n": 0}

    def _tick() -> datetime:
        i = state["n"]
        state["n"] = i + 1
        return base + timedelta(seconds=i)

    import app.workers.workspace_job_worker.worker as worker_mod

    monkeypatch.setattr(worker_mod, "_now", _tick)
