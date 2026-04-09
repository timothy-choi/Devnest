"""Topology DB integration tests: persist and query state only; skip real ``ip`` bridge ops.

CI/agents often lack privileges to create bridges; bridge behavior is covered in unit tests
with mocked runners and in environments that opt in to real Linux.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _skip_topology_linux_bridge_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVNEST_TOPOLOGY_SKIP_LINUX_BRIDGE", "1")
