"""Orchestrator app factory wires :class:`NodeExecutionBundle` into the default service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.node_execution_service.errors import NodeExecutionBindingError
from app.services.orchestrator_service.app_factory import build_default_orchestrator_for_session
from app.services.orchestrator_service.errors import AppOrchestratorBindingError


def test_build_default_orchestrator_passes_bundle_ensure_dir() -> None:
    session = MagicMock()
    bundle = MagicMock()
    bundle.docker_client = MagicMock()
    bundle.topology_command_runner = MagicMock()
    bundle.service_reachability_runner = None
    bundle.ensure_workspace_project_dir = MagicMock(return_value="/remote/devnest/7")

    with patch(
        "app.services.orchestrator_service.app_factory.resolve_node_execution_bundle",
        return_value=bundle,
    ) as mock_resolve:
        orch = build_default_orchestrator_for_session(
            session,
            execution_node_key="node-1",
            topology_id=3,
        )

    mock_resolve.assert_called_once_with(session, "node-1")
    assert orch._ensure_workspace_project_dir is bundle.ensure_workspace_project_dir


def test_build_default_orchestrator_wraps_node_execution_binding_error() -> None:
    session = MagicMock()
    with patch(
        "app.services.orchestrator_service.app_factory.resolve_node_execution_bundle",
        side_effect=NodeExecutionBindingError("no execution_node row for node_key='x'"),
    ):
        with pytest.raises(AppOrchestratorBindingError, match="no execution_node row"):
            build_default_orchestrator_for_session(session, execution_node_key="x")
