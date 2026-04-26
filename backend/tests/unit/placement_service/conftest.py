"""Shared fixtures for placement_service unit tests."""

from __future__ import annotations

import pytest

from app.libs.common.config import get_settings


@pytest.fixture
def enable_multi_node_scheduling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow multiple READY+schedulable nodes in tests that assert spread/capacity ordering."""
    monkeypatch.setenv("DEVNEST_ENABLE_MULTI_NODE_SCHEDULING", "true")
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("DEVNEST_ENABLE_MULTI_NODE_SCHEDULING", raising=False)
    get_settings.cache_clear()
