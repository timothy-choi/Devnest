"""Shared fixtures for placement_service unit tests."""

from __future__ import annotations

import pytest

from app.libs.common.config import get_settings


@pytest.fixture
def enable_multi_node_scheduling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force multi-node pool (``DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=true``); default app setting is false (Step 7)."""
    monkeypatch.setenv("DEVNEST_ENABLE_MULTI_NODE_SCHEDULING", "true")
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("DEVNEST_ENABLE_MULTI_NODE_SCHEDULING", raising=False)
    get_settings.cache_clear()


@pytest.fixture
def disable_multi_node_scheduling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restrict placement to primary execution node only (lowest id in READY+schedulable pool)."""
    monkeypatch.setenv("DEVNEST_ENABLE_MULTI_NODE_SCHEDULING", "false")
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("DEVNEST_ENABLE_MULTI_NODE_SCHEDULING", raising=False)
    get_settings.cache_clear()
