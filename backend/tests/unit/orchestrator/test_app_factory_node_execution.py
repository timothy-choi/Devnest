"""Orchestrator app factory wires :class:`NodeExecutionBundle` into the default service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.node_execution_service.errors import NodeExecutionBindingError
from app.libs.runtime.ssm_docker_runtime import SsmDockerRuntimeAdapter
from app.services.orchestrator_service.app_factory import build_default_orchestrator_for_session
from app.services.orchestrator_service.errors import AppOrchestratorBindingError


def test_build_default_orchestrator_passes_bundle_ensure_dir() -> None:
    session = MagicMock()
    bundle = MagicMock()
    bundle.docker_client = MagicMock()
    bundle.runtime_adapter = None
    bundle.topology_command_runner = MagicMock()
    bundle.service_reachability_runner = None
    bundle.traefik_routing_host = None
    bundle.defer_topology_attach = False
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
    assert orch._traefik_routing_host is None
    assert orch._remote_topology_attach_deferred is False


def test_build_default_orchestrator_passes_traefik_routing_host_from_bundle() -> None:
    session = MagicMock()
    bundle = MagicMock()
    bundle.docker_client = MagicMock()
    bundle.runtime_adapter = None
    bundle.topology_command_runner = MagicMock()
    bundle.service_reachability_runner = None
    bundle.traefik_routing_host = "10.20.30.40"
    bundle.defer_topology_attach = True
    bundle.ensure_workspace_project_dir = MagicMock(return_value="/x")

    with patch(
        "app.services.orchestrator_service.app_factory.resolve_node_execution_bundle",
        return_value=bundle,
    ):
        orch = build_default_orchestrator_for_session(session, execution_node_key="node-ec2")

    assert orch._traefik_routing_host == "10.20.30.40"
    assert orch._remote_topology_attach_deferred is True


def test_build_default_orchestrator_uses_runtime_adapter_when_set() -> None:
    session = MagicMock()
    bundle = MagicMock()
    bundle.docker_client = None
    bundle.runtime_adapter = SsmDockerRuntimeAdapter(MagicMock())
    bundle.topology_command_runner = MagicMock()
    bundle.service_reachability_runner = MagicMock()
    bundle.traefik_routing_host = None
    bundle.defer_topology_attach = True
    bundle.ensure_workspace_project_dir = MagicMock(return_value="/remote/ws/1")

    with patch(
        "app.services.orchestrator_service.app_factory.resolve_node_execution_bundle",
        return_value=bundle,
    ):
        orch = build_default_orchestrator_for_session(session, execution_node_key="ec2-node")

    assert orch._runtime_adapter is bundle.runtime_adapter
    assert orch._remote_topology_attach_deferred is True


def test_build_default_orchestrator_raises_when_no_docker_or_adapter() -> None:
    session = MagicMock()
    bundle = MagicMock()
    bundle.docker_client = None
    bundle.runtime_adapter = None
    bundle.topology_command_runner = MagicMock()
    bundle.service_reachability_runner = None
    bundle.traefik_routing_host = None
    bundle.defer_topology_attach = False
    bundle.ensure_workspace_project_dir = MagicMock()

    with patch(
        "app.services.orchestrator_service.app_factory.resolve_node_execution_bundle",
        return_value=bundle,
    ):
        with pytest.raises(AppOrchestratorBindingError, match="no runtime_adapter and no docker_client"):
            build_default_orchestrator_for_session(session, execution_node_key="broken")


def test_build_default_orchestrator_wraps_node_execution_binding_error() -> None:
    session = MagicMock()
    with patch(
        "app.services.orchestrator_service.app_factory.resolve_node_execution_bundle",
        side_effect=NodeExecutionBindingError("no execution_node row for node_key='x'"),
    ):
        with pytest.raises(AppOrchestratorBindingError, match="no execution_node row"):
            build_default_orchestrator_for_session(session, execution_node_key="x")
