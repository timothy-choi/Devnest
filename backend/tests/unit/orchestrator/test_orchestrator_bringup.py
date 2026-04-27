"""Unit tests: ``DefaultOrchestratorService.bring_up_workspace_runtime`` (mocked deps, orchestration only)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from app.libs.common.config import get_settings
from app.libs.probes.interfaces import ProbeRunner
from app.libs.probes.results import (
    ContainerProbeResult,
    HealthIssue,
    HealthIssueSeverity,
    ServiceProbeResult,
    WorkspaceHealthResult,
)
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
from app.libs.topology.models.enums import TopologyAttachmentStatus, TopologyRuntimeStatus
from app.libs.topology.results import (
    AllocateWorkspaceIPResult,
    AttachWorkspaceResult,
    DetachWorkspaceResult,
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


@pytest.fixture(autouse=True)
def _short_ide_tcp_bringup_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVNEST_WORKSPACE_BRINGUP_IDE_TCP_WAIT_SECONDS", "2.5")
    monkeypatch.setenv("DEVNEST_WORKSPACE_BRINGUP_IDE_TCP_POLL_INTERVAL_SECONDS", "0.1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
    mock_runtime.fetch_container_log_tail.return_value = ""


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
    mock_probe.check_service_reachable.return_value = ServiceProbeResult(
        healthy=True,
        workspace_ip=WORKSPACE_IP,
        port=WORKSPACE_IDE_CONTAINER_PORT,
        latency_ms=1.0,
        issues=(),
    )
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
    *,
    remote_topology_attach_deferred: bool = False,
    traefik_routing_host: str | None = None,
) -> DefaultOrchestratorService:
    return DefaultOrchestratorService(
        mock_runtime,
        mock_topology,
        mock_probe,
        topology_id=TOPOLOGY_ID,
        node_id=NODE_ID,
        workspace_projects_base=str(ws_root),
        traefik_routing_host=traefik_routing_host,
        remote_topology_attach_deferred=remote_topology_attach_deferred,
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
        host_path = str((Path(ws_root).resolve() / WORKSPACE_ID / "project"))

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
        # env now includes code-server defaults; extra_bind_mounts includes CS persistence paths.
        ensure_call = mock_runtime.mock_calls[0]
        assert ensure_call[0] == "ensure_container"
        ensure_kwargs = ensure_call[2]
        assert ensure_kwargs["name"] == f"devnest-ws-{WORKSPACE_ID}"
        assert ensure_kwargs["image"] is None
        assert ensure_kwargs["cpu_limit"] is None
        assert ensure_kwargs["memory_limit_bytes"] is None
        # Code-server env is injected automatically.
        assert isinstance(ensure_kwargs["env"], dict)
        assert ensure_kwargs["env"].get("CODE_SERVER_AUTH") == "none"
        assert ensure_kwargs["env"].get("PORT") == "8080"
        assert ensure_kwargs["ports"] == ((0, WORKSPACE_IDE_CONTAINER_PORT),)
        assert ensure_kwargs["workspace_host_path"] == host_path
        # Code-server bind mounts are injected.
        extra = ensure_kwargs.get("extra_bind_mounts") or []
        container_paths = [m.container_path for m in extra]
        assert "/home/coder/.config/code-server" in container_paths
        assert "/home/coder/.local/share/code-server" in container_paths
        assert mock_runtime.mock_calls[1] == call.start_container(container_id=CONTAINER_ID)
        names = [c[0] for c in mock_runtime.mock_calls]
        assert names.count("get_container_netns_ref") == 2
        assert names.count("inspect_container") >= 3  # post-start wait + two pre-topology liveness gates

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

        mock_probe.check_service_reachable.assert_called()
        mock_probe.check_workspace_health.assert_called_once_with(
            workspace_id=WORKSPACE_ID,
            topology_id=str(TOPOLOGY_ID),
            node_id=NODE_ID,
            container_id=CONTAINER_ID,
            expected_port=WORKSPACE_IDE_CONTAINER_PORT,
            timeout_seconds=8.0,
        )

    def test_remote_node_skips_local_topology_attach_and_uses_host_port_probe(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _runtime_ok(mock_runtime)
        mock_probe.check_container_running.return_value = ContainerProbeResult(
            healthy=True,
            container_id=CONTAINER_ID,
            container_state="running",
            issues=(),
        )
        mock_probe.check_service_http.return_value = ServiceProbeResult(
            healthy=True,
            workspace_ip="127.0.0.1",
            port=32000,
            latency_ms=1.0,
            issues=(),
        )

        svc = _make_service(
            mock_runtime,
            mock_topology,
            mock_probe,
            ws_root,
            remote_topology_attach_deferred=True,
            traefik_routing_host="10.0.1.20",
        )

        out = svc.bring_up_workspace_runtime(workspace_id=WORKSPACE_ID)

        assert out.success is True
        assert out.workspace_ip is None
        assert out.internal_endpoint == "127.0.0.1:32000"
        assert out.gateway_route_target == "http://10.0.1.20:32000"
        assert out.netns_ref == "/devnest-skip-linux-topology-attachment"
        mock_topology.ensure_node_topology.assert_not_called()
        mock_topology.allocate_workspace_ip.assert_not_called()
        mock_topology.attach_workspace.assert_not_called()
        mock_runtime.get_container_netns_ref.assert_not_called()
        reach_kwargs = mock_probe.check_service_http.call_args.kwargs
        assert reach_kwargs["workspace_ip"] == "127.0.0.1"
        assert reach_kwargs["port"] == 32000
        assert reach_kwargs["timeout_seconds"] > 0
        mock_probe.check_service_reachable.assert_not_called()
        mock_probe.check_workspace_health.assert_not_called()

    def test_same_workspace_id_with_different_storage_keys_uses_different_project_paths(
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

        svc.bring_up_workspace_runtime(
            workspace_id=WORKSPACE_ID,
            project_storage_key="run-a",
            launch_mode="new",
        )
        first_path = mock_runtime.ensure_container.call_args.kwargs["workspace_host_path"]

        mock_runtime.reset_mock()
        _runtime_ok(mock_runtime)
        svc.bring_up_workspace_runtime(
            workspace_id=WORKSPACE_ID,
            project_storage_key="run-b",
            launch_mode="new",
        )
        second_path = mock_runtime.ensure_container.call_args.kwargs["workspace_host_path"]

        assert first_path != second_path
        assert first_path.endswith(f"{WORKSPACE_ID}-run-a/project")
        assert second_path.endswith(f"{WORKSPACE_ID}-run-b/project")

    def test_same_workspace_resume_reuses_same_project_path(
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

        svc.bring_up_workspace_runtime(
            workspace_id=WORKSPACE_ID,
            project_storage_key="persist-me",
            launch_mode="new",
        )
        first_path = mock_runtime.ensure_container.call_args.kwargs["workspace_host_path"]

        mock_runtime.reset_mock()
        _runtime_ok(mock_runtime)
        svc.bring_up_workspace_runtime(
            workspace_id=WORKSPACE_ID,
            project_storage_key="persist-me",
            launch_mode="resume",
        )
        second_path = mock_runtime.ensure_container.call_args.kwargs["workspace_host_path"]

        assert first_path == second_path

    def test_resume_raises_when_project_dir_missing(
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
        with pytest.raises(WorkspaceBringUpError, match="missing for resume"):
            svc.bring_up_workspace_runtime(
                workspace_id=WORKSPACE_ID,
                project_storage_key="no-such-dir-on-disk",
                launch_mode="resume",
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


class TestBringUpWorkspaceDeadBeforeTopology:
    def test_exited_before_ensure_topology_skips_attach(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _runtime_ok(mock_runtime)
        ins_running = ContainerInspectionResult(
            exists=True,
            container_id=CONTAINER_ID,
            container_state="running",
            pid=12345,
            ports=((32000, WORKSPACE_IDE_CONTAINER_PORT),),
            mounts=(),
        )
        ins_exited = ContainerInspectionResult(
            exists=True,
            container_id=CONTAINER_ID,
            container_state="exited",
            pid=None,
            ports=(),
            mounts=(),
        )
        mock_runtime.inspect_container.side_effect = [ins_running, ins_exited]

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceBringUpError, match="exited before topology attach"):
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
        # First call: ``ensure_running_runtime_only``; second: attach handoff — wrapped as bring-up error.
        with pytest.raises(WorkspaceBringUpError, match="workspace runtime not ready for topology"):
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
        mock_probe.check_service_reachable.return_value = ServiceProbeResult(
            healthy=False,
            workspace_ip=WORKSPACE_IP,
            port=WORKSPACE_IDE_CONTAINER_PORT,
            latency_ms=None,
            issues=(
                HealthIssue(
                    code="SERVICE_UNREACHABLE",
                    component="service",
                    message="remote nc probe failed",
                    severity=HealthIssueSeverity.ERROR,
                ),
            ),
        )
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
        mock_topology.detach_workspace.return_value = DetachWorkspaceResult(
            detached=True,
            status=TopologyAttachmentStatus.DETACHED,
            workspace_id=int(WORKSPACE_ID),
            workspace_ip=WORKSPACE_IP,
            released_ip=False,
        )
        mock_runtime.stop_container.return_value = RuntimeActionResult(
            container_id=CONTAINER_ID,
            container_state="exited",
            success=True,
            message=None,
        )
        mock_topology.release_workspace_ip_lease.return_value = True

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        out = svc.bring_up_workspace_runtime(workspace_id=WORKSPACE_ID)

        assert out.success is False
        assert out.probe_healthy is False
        assert out.issues == ["service:SERVICE_TIMEOUT:TCP timed out"]
        assert out.container_id == CONTAINER_ID
        assert out.netns_ref == NETNS_REF
        assert out.rollback_attempted is True
        assert out.rollback_succeeded is True
        assert not out.rollback_issues
        assert mock_probe.check_service_reachable.call_count >= 2
        mock_probe.check_workspace_health.assert_called_once()
        assert mock_topology.detach_workspace.call_count >= 1
        assert mock_runtime.stop_container.call_count >= 1
        mock_topology.release_workspace_ip_lease.assert_called_once_with(
            topology_id=TOPOLOGY_ID,
            node_id=NODE_ID,
            workspace_id=int(WORKSPACE_ID),
        )

    def test_probe_unhealthy_when_inner_stop_fails_after_retry(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _runtime_ok(mock_runtime)
        _topology_ok(mock_topology)
        mock_probe.check_service_reachable.return_value = ServiceProbeResult(
            healthy=False,
            workspace_ip=WORKSPACE_IP,
            port=WORKSPACE_IDE_CONTAINER_PORT,
            latency_ms=None,
            issues=(
                HealthIssue(
                    code="SERVICE_UNREACHABLE",
                    component="service",
                    message="remote nc probe failed",
                    severity=HealthIssueSeverity.ERROR,
                ),
            ),
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
            issues=(
                HealthIssue(
                    code="SERVICE_TIMEOUT",
                    component="service",
                    message="TCP timed out",
                    severity=HealthIssueSeverity.ERROR,
                ),
            ),
        )
        mock_topology.detach_workspace.return_value = DetachWorkspaceResult(
            detached=True,
            status=TopologyAttachmentStatus.DETACHED,
            workspace_id=int(WORKSPACE_ID),
            workspace_ip=WORKSPACE_IP,
            released_ip=False,
        )
        mock_runtime.stop_container.return_value = RuntimeActionResult(
            container_id=CONTAINER_ID,
            container_state="running",
            success=False,
            message="engine refused stop",
        )
        mock_topology.release_workspace_ip_lease.return_value = True

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        out = svc.bring_up_workspace_runtime(workspace_id=WORKSPACE_ID)

        assert out.success is False
        assert out.rollback_attempted is True
        assert out.rollback_succeeded is False
        assert out.rollback_issues
        assert any("rollback:stop_incomplete" in x for x in (out.rollback_issues or []))
        assert mock_runtime.stop_container.call_count >= 2
