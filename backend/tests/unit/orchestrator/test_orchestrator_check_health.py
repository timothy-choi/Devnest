"""Unit tests: ``DefaultOrchestratorService.check_workspace_runtime_health`` (read-only, mocked deps)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.libs.probes.interfaces import ProbeRunner
from app.libs.probes.results import WorkspaceHealthResult
from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.runtime.models import ContainerInspectionResult, NetnsRefResult
from app.libs.topology.interfaces import TopologyAdapter
from app.services.orchestrator_service import DefaultOrchestratorService
from app.services.orchestrator_service.errors import WorkspaceBringUpError

WORKSPACE_ID = "123"
CONTAINER_REF = f"devnest-ws-{WORKSPACE_ID}"
CONTAINER_ID = "cid-health"
NODE_ID = "node-1"
TOPOLOGY_ID = 1


@pytest.fixture
def mock_runtime() -> MagicMock:
    return MagicMock(spec=RuntimeAdapter)


@pytest.fixture
def mock_topology() -> MagicMock:
    return MagicMock(spec=TopologyAdapter)


@pytest.fixture
def mock_probe() -> MagicMock:
    return MagicMock(spec=ProbeRunner)


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "devnest-workspaces"


def _make_service(
    mock_runtime: MagicMock,
    mock_topology: MagicMock,
    mock_probe: MagicMock,
    ws_root: Path,
) -> DefaultOrchestratorService:
    return DefaultOrchestratorService(
        mock_runtime,
        mock_topology,
        mock_probe,
        topology_id=TOPOLOGY_ID,
        node_id=NODE_ID,
        workspace_projects_base=str(ws_root),
    )


def _inspect_running() -> ContainerInspectionResult:
    return ContainerInspectionResult(
        exists=True,
        container_id=CONTAINER_ID,
        container_state="running",
        pid=1,
        ports=(),
        mounts=(),
        labels=(),
    )


class TestCheckHealth:
    def test_running_container_delegates_to_probe(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        mock_runtime.inspect_container.return_value = _inspect_running()
        mock_runtime.get_container_netns_ref.return_value = NetnsRefResult(
            container_id=CONTAINER_ID,
            pid=42,
            netns_ref="/proc/42/ns/net",
        )
        mock_probe.check_workspace_health.return_value = WorkspaceHealthResult(
            workspace_id=int(WORKSPACE_ID),
            healthy=True,
            runtime_healthy=True,
            topology_healthy=True,
            service_healthy=True,
            container_state="running",
            workspace_ip="10.0.0.2",
            internal_endpoint="10.0.0.2:8080",
            issues=(),
        )

        out = svc.check_workspace_runtime_health(workspace_id=WORKSPACE_ID)

        mock_topology.assert_not_called()
        mock_probe.check_workspace_health.assert_called_once()
        assert out.success is True
        assert out.probe_healthy is True
        assert out.container_id == CONTAINER_ID
        assert out.netns_ref == "/proc/42/ns/net"
        assert out.issues is None or out.issues == []

    def test_missing_container_returns_failed_result_without_probe(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        mock_runtime.inspect_container.return_value = ContainerInspectionResult(
            exists=False,
            container_id=None,
            container_state="missing",
            pid=None,
            ports=(),
            mounts=(),
            labels=(),
        )

        out = svc.check_workspace_runtime_health(workspace_id=WORKSPACE_ID)

        mock_probe.check_workspace_health.assert_not_called()
        assert out.success is False
        assert out.issues and "not_found" in out.issues[0]

    def test_empty_workspace_id_raises(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceBringUpError, match="empty"):
            svc.check_workspace_runtime_health(workspace_id="  ")

    def test_strict_requires_container_id_before_inspect(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with patch(
            "app.services.orchestrator_service.service.authoritative_container_ref_required",
            return_value=True,
        ):
            out = svc.check_workspace_runtime_health(workspace_id=WORKSPACE_ID, container_id=None)

        mock_runtime.inspect_container.assert_not_called()
        mock_probe.check_workspace_health.assert_not_called()
        assert out.success is False
        assert out.issues
        assert "authoritative_container_id_required" in out.issues[0]
