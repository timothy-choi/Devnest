"""Unit tests for autoscaler drain delay and recent-activity window (Task 4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session

from app.services.autoscaler_service.service import (
    _find_draining_node_past_delay,
    _node_has_recent_activity,
)
from app.services.placement_service.models import ExecutionNode, ExecutionNodeProviderType, ExecutionNodeStatus


def _make_node(
    node_key: str,
    status: str = ExecutionNodeStatus.DRAINING.value,
    updated_at: datetime | None = None,
) -> ExecutionNode:
    node = MagicMock(spec=ExecutionNode)
    node.node_key = node_key
    node.status = status
    node.provider_type = ExecutionNodeProviderType.EC2.value
    node.updated_at = updated_at or datetime.now(timezone.utc)
    node.provider_instance_id = "i-test"
    return node


class TestFindDrainingNodePastDelay:
    def test_returns_none_when_no_draining_nodes(self) -> None:
        session = MagicMock(spec=Session)
        session.exec.return_value.all.return_value = []
        result = _find_draining_node_past_delay(session, drain_delay_seconds=30)
        assert result is None

    def test_returns_node_when_drain_delay_zero(self) -> None:
        session = MagicMock(spec=Session)
        node = _make_node("node-1")
        session.exec.return_value.first.return_value = node
        result = _find_draining_node_past_delay(session, drain_delay_seconds=0)
        assert result is node

    def test_returns_none_when_node_drained_too_recently(self) -> None:
        session = MagicMock(spec=Session)
        recently = datetime.now(timezone.utc) - timedelta(seconds=10)
        node = _make_node("node-1", updated_at=recently)
        session.exec.return_value.all.return_value = [node]
        result = _find_draining_node_past_delay(session, drain_delay_seconds=30)
        assert result is None

    def test_returns_node_when_past_drain_delay(self) -> None:
        session = MagicMock(spec=Session)
        old_time = datetime.now(timezone.utc) - timedelta(seconds=60)
        node = _make_node("node-1", updated_at=old_time)
        session.exec.return_value.all.return_value = [node]
        result = _find_draining_node_past_delay(session, drain_delay_seconds=30)
        assert result is node

    def test_picks_first_eligible_sorted_by_node_key(self) -> None:
        session = MagicMock(spec=Session)
        old_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        node_a = _make_node("node-a", updated_at=old_time)
        node_b = _make_node("node-b", updated_at=old_time)
        session.exec.return_value.all.return_value = [node_a, node_b]
        result = _find_draining_node_past_delay(session, drain_delay_seconds=60)
        assert result is node_a

    def test_returns_node_with_none_updated_at(self) -> None:
        """Nodes with no updated_at timestamp are always eligible."""
        session = MagicMock(spec=Session)
        node = _make_node("node-1", updated_at=None)
        node.updated_at = None
        session.exec.return_value.all.return_value = [node]
        result = _find_draining_node_past_delay(session, drain_delay_seconds=300)
        assert result is node


class TestConfig:
    def test_settings_have_drain_delay(self) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415
        s = Settings(database_url="postgresql://x:y@h/d")
        assert hasattr(s, "devnest_autoscaler_drain_delay_seconds")
        assert s.devnest_autoscaler_drain_delay_seconds == 30

    def test_settings_have_activity_window(self) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415
        s = Settings(database_url="postgresql://x:y@h/d")
        assert hasattr(s, "devnest_autoscaler_recent_activity_window_seconds")
        assert s.devnest_autoscaler_recent_activity_window_seconds == 300

    def test_drain_delay_coercion_negative(self) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415
        import os  # noqa: PLC0415
        s = Settings(
            database_url="postgresql://x:y@h/d",
            devnest_autoscaler_drain_delay_seconds=-5,
        )
        assert s.devnest_autoscaler_drain_delay_seconds == 0
