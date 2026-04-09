"""Unit topology tests: avoid real ``ip`` calls (no Linux bridge in SQLite-only runs)."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _skip_topology_linux_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVNEST_TOPOLOGY_SKIP_LINUX_BRIDGE", "1")
