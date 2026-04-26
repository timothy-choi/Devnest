"""Unit tests: scheduler service (mocked placement)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.libs.common.config import get_settings
from app.libs.observability.log_events import LogEvent
from app.services.placement_service.errors import InvalidPlacementParametersError, NoSchedulableNodeError
from app.services.placement_service.models import ExecutionNode, ExecutionNodeProviderType, ExecutionNodeStatus
from app.services.scheduler_service.service import schedule_workspace


def _chosen() -> ExecutionNode:
    return ExecutionNode(
        node_key="node-a",
        name="node-a",
        provider_type=ExecutionNodeProviderType.LOCAL.value,
        status=ExecutionNodeStatus.READY.value,
        schedulable=True,
        total_cpu=4.0,
        total_memory_mb=8192,
        allocatable_cpu=4.0,
        allocatable_memory_mb=8192,
        allocatable_disk_mb=102_400,
        max_workspaces=32,
        default_topology_id=7,
    )


@patch("app.services.scheduler_service.service.reserve_node_for_workspace")
def test_schedule_workspace_success(mock_reserve: MagicMock) -> None:
    mock_reserve.return_value = _chosen()
    session = MagicMock()
    out = schedule_workspace(session, workspace_id=42)
    assert out.execution_node is not None
    assert out.execution_node.node_key == "node-a"
    assert out.insufficient_capacity is False
    assert out.invalid_request is False
    mock_reserve.assert_called_once_with(
        session,
        workspace_id=42,
        requested_cpu=pytest.approx(1.0),
        requested_memory_mb=512,
        requested_disk_mb=4096,
    )


@patch("app.services.scheduler_service.service.reserve_node_for_workspace")
def test_schedule_workspace_no_capacity(mock_reserve: MagicMock) -> None:
    mock_reserve.side_effect = NoSchedulableNodeError("no nodes")
    out = schedule_workspace(MagicMock(), workspace_id=1)
    assert out.execution_node is None
    assert out.insufficient_capacity is True
    assert "no nodes" in out.message


@patch("app.services.scheduler_service.service.reserve_node_for_workspace")
def test_schedule_workspace_invalid_request(mock_reserve: MagicMock) -> None:
    mock_reserve.side_effect = InvalidPlacementParametersError("bad mem")
    out = schedule_workspace(MagicMock(), workspace_id=1)
    assert out.execution_node is None
    assert out.invalid_request is True


@patch("app.services.scheduler_service.service.log_event")
@patch("app.services.scheduler_service.service.reserve_node_for_workspace")
def test_schedule_workspace_success_logs_single_node_gate_when_disabled(
    mock_reserve: MagicMock,
    mock_log: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVNEST_ENABLE_MULTI_NODE_SCHEDULING", "false")
    get_settings.cache_clear()
    mock_reserve.return_value = _chosen()
    schedule_workspace(MagicMock(), workspace_id=42)
    sel = next(c for c in mock_log.call_args_list if len(c.args) > 1 and c.args[1] == LogEvent.SCHEDULER_NODE_SELECTED)
    kwargs = sel.kwargs
    assert kwargs["multi_node_scheduling_enabled"] is False
    assert kwargs["placement_single_node_gate"] is True
    assert kwargs["workspace_id"] == 42
    get_settings.cache_clear()


@patch("app.services.scheduler_service.service.explain_placement_decision", return_value="rank-1 | pool-2")
@patch("app.services.scheduler_service.service.log_event")
@patch("app.services.scheduler_service.service.reserve_node_for_workspace")
def test_schedule_workspace_logs_placement_decision_summary(
    mock_reserve: MagicMock,
    mock_log: MagicMock,
    _mock_explain: MagicMock,
) -> None:
    mock_reserve.return_value = _chosen()
    schedule_workspace(MagicMock(), workspace_id=501)
    names = [call_args[0][1] for call_args in mock_log.call_args_list]
    assert LogEvent.SCHEDULER_NODE_SELECTED in names
    assert LogEvent.PLACEMENT_DECISION_SUMMARY in names


@patch("app.services.scheduler_service.service.log_event")
@patch("app.services.scheduler_service.service.reserve_node_for_workspace")
def test_schedule_workspace_success_logs_multi_node_pool_by_default(
    mock_reserve: MagicMock,
    mock_log: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEVNEST_ENABLE_MULTI_NODE_SCHEDULING", raising=False)
    get_settings.cache_clear()
    mock_reserve.return_value = _chosen()
    schedule_workspace(MagicMock(), workspace_id=99)
    sel = next(c for c in mock_log.call_args_list if len(c.args) > 1 and c.args[1] == LogEvent.SCHEDULER_NODE_SELECTED)
    kwargs = sel.kwargs
    assert kwargs["multi_node_scheduling_enabled"] is True
    assert kwargs["placement_single_node_gate"] is False
    assert kwargs["workspace_id"] == 99
    get_settings.cache_clear()
