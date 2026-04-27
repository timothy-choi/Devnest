"""Unit tests: execution_node_heartbeat_age_seconds (Step 12 ops telemetry)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.services.placement_service.node_heartbeat import execution_node_heartbeat_age_seconds


def test_execution_node_heartbeat_age_seconds_none_when_no_timestamp() -> None:
    n = MagicMock()
    n.last_heartbeat_at = None
    assert execution_node_heartbeat_age_seconds(n) is None


def test_execution_node_heartbeat_age_seconds_naive_timestamp_treated_as_utc() -> None:
    n = MagicMock()
    n.last_heartbeat_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=10)
    age = execution_node_heartbeat_age_seconds(n)
    assert age is not None
    assert 8 <= age <= 15
