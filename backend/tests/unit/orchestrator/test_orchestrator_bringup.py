"""Unit tests: ``DefaultOrchestratorService.bring_up_workspace_runtime`` (mocked deps, orchestration only)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from app.libs.probes.interfaces import ProbeRunner
from app.libs.probes.results import HealthIssue, HealthIssueSeverity, WorkspaceHealthResult
from app.libs.runtime.errors import NetnsRefError
from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.runtime.models import (
    ContainerInspectionResult,
    NetnsRefResult,
    RuntimeActionResult,
    RuntimeEnsureResult,
    WORKSPACE_IDE_CONTAINER_PORT,
)
from app.libs.topology.errors import (
    TopologyRuntimeCreateError,
    WorkspaceAttachmentError,
    WorkspaceIPAllocationError,
)
from app.libs.topology.interfaces import TopologyAdapter
from app.libs.topology.models.enums import TopologyRuntimeStatus
from app.libs.topology.results import (
    AllocateWorkspaceIPResult,
    AttachWorkspaceResult,
    EnsureNodeTopologyResult,
)
from app.services.orchestrator_service import DefaultOrchestratorService
from app.services.orchestrator_service.errors import WorkspaceBringUpError

# V1 orchestrator parses workspace_id as a non-negative int for topology; use a numeric string.
WORKSPACE_ID = "123"
CONTAINER_ID = "container-abc"
NODE_ID = "node-a"
TOPOLOGY_ID = 1
NETNS_REF = "/proc/12345/ns/net"
WORKSPACE_IP = "10.128.0.10"
INTERNAL_ENDPOINT = f"{WORKSPACE_IP}:8080"


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


def _runtime_ok(mock_runtime: MagicMock) -> None:
    mock_runtime.ensure_container.return_value = RuntimeEnsureResult(
        container_id=CONTAINER_ID,
        exists=True,
        created_new=True,
        container_state="running",
        resolved_ports=((32000, WORKSPACE_IDE_CONTAINER_PORT),),
    )
    mock_runtime.start_container.return_value = RuntimeActionResult(
        container_id=CONTAINER_ID,
        container_state="running",
        success=True,
        message=None,
    )
    mock_runtime.inspect_container.return_value = ContainerInspectionResult(
        exists=True,
        container_id=CONTAINER_ID,
        container_state="running",
        pid=12345,
        ports=((32000, WORKSPACE_IDE_CONTAINER_PORT),),
        mounts=(),
    )
    mock_runtime.get_container_netns_ref.return_value = NetnsRefResult(
        container_id=CONTAINER_ID,
        pid=12345,
        netns_ref=NETNS_REF,
    )


def _topology_ok(mock_topology: MagicMock) -> None:
    mock_topology.ensure_node_topology.return_value = EnsureNodeTopologyResult(
        topology_runtime_id=1,
        bridge_name="br-test",
        cidr="10.128.0.0/24",
        gateway_ip="10.128.0.1",
        status=TopologyRuntimeStatus.READY,
    )
    mock_topology.allocate_workspace_ip.return_value = AllocateWorkspaceIPResult(
        workspace_ip=WORKSPACE_IP,
        leased_existing=False,
    )
    mock_topology.attach_workspace.return_value = AttachWorkspaceResult(
        attachment_id=99,
        workspace_ip=WORKSPACE_IP,
        bridge_name="br-test",
        gateway_ip="10.128.0.1",
        internal_endpoint=INTERNAL_ENDPOINT,
    )


def _probe_ok(mock_probe: MagicMock) -> None:
    mock_probe.check_workspace_health.return_value = WorkspaceHealthResult(
        workspace_id=int(WORKSPACE_ID),
        healthy=True,
        runtime_healthy=True,
        topology_healthy=True,
        service_healthy=True,
        container_state="running",
        workspace_ip=WORKSPACE_IP,
        internal_endpoint=INTERNAL_ENDPOINT,
        issues=(),
    )


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


class TestBringUpHappyPath:
    def test_calls_dependencies_in_order_and_returns_success(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _runtime_ok(mock_runtime)
        _topology_ok(mock_topology)
        _probe_ok(mock_probe)

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        host_path = str(Path(ws_root).resolve() / WORKSPACE_ID)

        out = svc.bring_up_workspace_runtime(workspace_id=WORKSPACE_ID)

        assert out.success is True
        assert out.probe_healthy is True
        assert out.container_id == CONTAINER_ID
        assert out.container_state == "running"
        assert out.netns_ref == NETNS_REF
        assert out.workspace_ip == WORKSPACE_IP
        assert out.internal_endpoint == INTERNAL_ENDPOINT
        assert out.node_id == NODE_ID
        assert out.topology_id == str(TOPOLOGY_ID)
        assert out.workspace_id == WORKSPACE_ID
        assert out.issues is None or out.issues == []

        # Runtime: ensure_running_runtime_only order, then second netns read before attach.
        assert mock_runtime.mock_calls == [
            call.ensure_container(
                name=f"devnest-ws-{WORKSPACE_ID}",
                image=None,
                cpu_limit=None,
                memory_limit_bytes=None,
                env=None,
                ports=((0, WORKSPACE_IDE_CONTAINER_PORT),),
                labels={
                    "devnest.workspace_id": WORKSPACE_ID,
                    "devnest.managed_by": "orchestrator",
                },
                project_mount=None,
                workspace_host_path=host_path,
                extra_bind_mounts=None,
                existing_container_id=None,
            ),
            call.start_container(container_id=CONTAINER_ID),
            call.inspect_container(container_id=CONTAINER_ID),
            call.get_container_netns_ref(container_id=CONTAINER_ID),
            call.get_container_netns_ref(container_id=CONTAINER_ID),
        ]

        mock_topology.ensure_node_topology.assert_called_once_with(
            topology_id=TOPOLOGY_ID,
            node_id=NODE_ID,
        )
        mock_topology.allocate_workspace_ip.assert_called_once_with(
            topology_id=TOPOLOGY_ID,
            node_id=NODE_ID,
            workspace_id=int(WORKSPACE_ID),
        )
        mock_topology.attach_workspace.assert_called_once_with(
            topology_id=TOPOLOGY_ID,
            node_id=NODE_ID,
            workspace_id=int(WORKSPACE_ID),
            container_id=CONTAINER_ID,
            netns_ref=NETNS_REF,
            workspace_ip=WORKSPACE_IP,
        )

        mock_probe.check_workspace_health.assert_called_once_with(
            workspace_id=WORKSPACE_ID,
            topology_id=str(TOPOLOGY_ID),
            node_id=NODE_ID,
            container_id=CONTAINER_ID,
            expected_port=WORKSPACE_IDE_CONTAINER_PORT,
            timeout_seconds=5.0,
        )


class TestBringUpRuntimeFailures:
    def test_ensure_empty_container_id_raises_and_skips_topology_and_probe(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        mock_runtime.ensure_container.return_value = RuntimeEnsureResult(
            container_id="",
            exists=False,
            created_new=False,
            container_state="created",
            resolved_ports=(),
        )

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceBringUpError, match="runtime bring-up failed"):
            svc.bring_up_workspace_runtime(workspace_id=WORKSPACE_ID)

        mock_runtime.start_container.assert_not_called()
        mock_topology.ensure_node_topology.assert_not_called()
        mock_probe.check_workspace_health.assert_not_called()

    def test_start_failure_raises_and_skips_topology_and_probe(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        mock_runtime.ensure_container.return_value = RuntimeEnsureResult(
            container_id=CONTAINER_ID,
            exists=True,
            created_new=True,
            container_state="created",
            resolved_ports=(),
        )
        mock_runtime.start_container.return_value = RuntimeActionResult(
            container_id=CONTAINER_ID,
            container_state="created",
            success=False,
            message="engine refused start",
        )

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceBringUpError, match="runtime bring-up failed"):
            svc.bring_up_workspace_runtime(workspace_id=WORKSPACE_ID)

        mock_topology.ensure_node_topology.assert_not_called()
        mock_probe.check_workspace_health.assert_not_called()


class TestBringUpNetnsFailureBeforeAttach:
    def test_second_get_netns_ref_failure_skips_attach_and_probe(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _runtime_ok(mock_runtime)
        _topology_ok(mock_topology)

        calls = {"n": 0}

        def netns_side_effect(*_a: object, **_kw: object) -> NetnsRefResult:
            calls["n"] += 1
            if calls["n"] == 1:
                return NetnsRefResult(
                    container_id=CONTAINER_ID,
                    pid=12345,
                    netns_ref=NETNS_REF,
                )
            raise NetnsRefError("stale pid")

        mock_runtime.get_container_netns_ref.side_effect = netns_side_effect

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        # Second get_container_netns_ref runs inside the topology try; NetnsRefError is not wrapped
        # as WorkspaceBringUpError (only TopologyError is).
        with pytest.raises(NetnsRefError, match="stale pid"):
            svc.bring_up_workspace_runtime(workspace_id=WORKSPACE_ID)

        mock_topology.ensure_node_topology.assert_called_once()
        mock_topology.allocate_workspace_ip.assert_called_once()
        mock_topology.attach_workspace.assert_not_called()
        mock_probe.check_workspace_health.assert_not_called()


class TestBringUpTopologyFailures:
    def test_ensure_node_topology_failure_skips_downstream(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _runtime_ok(mock_runtime)
        mock_topology.ensure_node_topology.side_effect = TopologyRuntimeCreateError("bridge failed")

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceBringUpError, match="topology bring-up failed"):
            svc.bring_up_workspace_runtime(workspace_id=WORKSPACE_ID)

        mock_topology.allocate_workspace_ip.assert_not_called()
        mock_topology.attach_workspace.assert_not_called()
        mock_probe.check_workspace_health.assert_not_called()

    def test_allocate_workspace_ip_failure_skips_attach_and_probe(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _runtime_ok(mock_runtime)
        mock_topology.ensure_node_topology.return_value = EnsureNodeTopologyResult(
            topology_runtime_id=1,
            bridge_name="br-test",
            cidr="10.128.0.0/24",
            gateway_ip="10.128.0.1",
            status=TopologyRuntimeStatus.READY,
        )
        mock_topology.allocate_workspace_ip.side_effect = WorkspaceIPAllocationError("pool exhausted")

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceBringUpError, match="topology bring-up failed"):
            svc.bring_up_workspace_runtime(workspace_id=WORKSPACE_ID)

        mock_topology.attach_workspace.assert_not_called()
        mock_probe.check_workspace_health.assert_not_called()

    def test_attach_workspace_failure_skips_probe(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _runtime_ok(mock_runtime)
        _topology_ok(mock_topology)
        mock_topology.attach_workspace.side_effect = WorkspaceAttachmentError("veth failed")

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceBringUpError, match="topology bring-up failed"):
            svc.bring_up_workspace_runtime(workspace_id=WORKSPACE_ID)

        mock_probe.check_workspace_health.assert_not_called()


class TestBringUpProbeUnhealthy:
    def test_unhealthy_probe_returns_result_with_failure_flags_and_issues(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _runtime_ok(mock_runtime)
        _topology_ok(mock_topology)
        issue = HealthIssue(
            code="SERVICE_TIMEOUT",
            component="service",
            message="TCP timed out",
            severity=HealthIssueSeverity.ERROR,
        )
        mock_probe.check_workspace_health.return_value = WorkspaceHealthResult(
            workspace_id=int(WORKSPACE_ID),
            healthy=False,
            runtime_healthy=True,
            topology_healthy=True,
            service_healthy=False,
            container_state="running",
            workspace_ip=WORKSPACE_IP,
            internal_endpoint=INTERNAL_ENDPOINT,
            issues=(issue,),
        )

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        out = svc.bring_up_workspace_runtime(workspace_id=WORKSPACE_ID)

        assert out.success is False
        assert out.probe_healthy is False
        assert out.issues == ["service:SERVICE_TIMEOUT:TCP timed out"]
        assert out.container_id == CONTAINER_ID
        assert out.netns_ref == NETNS_REF
        mock_probe.check_workspace_health.assert_called_once()
