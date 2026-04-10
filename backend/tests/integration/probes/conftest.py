"""Probe runner integration: align with topology DB tests (no host bridge/netns)."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _skip_linux_topology_for_probe_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVNEST_TOPOLOGY_SKIP_LINUX_BRIDGE", "1")
    monkeypatch.setenv("DEVNEST_TOPOLOGY_SKIP_LINUX_ATTACHMENT", "1")
