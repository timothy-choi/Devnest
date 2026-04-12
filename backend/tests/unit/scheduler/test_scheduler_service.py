"""Unit tests: scheduler service (mocked placement)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

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
