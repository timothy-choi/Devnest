"""Unit tests: ``DefaultOrchestratorService.update_workspace_runtime``.

V1 ``workspace_id`` must be a non-negative base-10 integer string (topology parsing). The prompt
example ``\"ws-123\"`` is invalid; we use ``\"123\"`` as the logical workspace id and align other
constants with the requested fixtures (``container-abc``, ``10.128.0.10``, ``topo-1``, …).

Update compares the engine label ``devnest.config_version`` (via ``inspect_container``) to
``requested_config_version``. On mismatch it delegates to ``restart_workspace_runtime``, which
sequences ``stop_workspace_runtime`` then ``bring_up_workspace_runtime`` — tests verify that order
by patching those methods while leaving ``restart_workspace_runtime`` real.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.libs.probes.interfaces import ProbeRunner
from app.libs.probes.results import WorkspaceHealthResult
from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.runtime.models import ContainerInspectionResult
from app.libs.topology.interfaces import TopologyAdapter
from app.services.orchestrator_service import DefaultOrchestratorService
from app.services.orchestrator_service.errors import (
    WorkspaceBringUpError,
    WorkspaceRestartError,
    WorkspaceUpdateError,
)
from app.services.orchestrator_service.results import (
    WorkspaceBringUpResult,
    WorkspaceRestartResult,
    WorkspaceStopResult,
)

WORKSPACE_ID = "123"
CONTAINER_ID = "container-abc"
CONTAINER_REF = f"devnest-ws-{WORKSPACE_ID}"
NODE_ID = "node-1"
SERVICE_TOPOLOGY_ID = 1
TOPOLOGY_ID_BRINGUP = "topo-1"
WORKSPACE_IP = "10.128.0.10"
INTERNAL_ENDPOINT = "10.128.0.10:8080"
NETNS_REF = "/proc/12345/ns/net"
CONFIG_LABEL = "devnest.config_version"


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
        topology_id=SERVICE_TOPOLOGY_ID,
        node_id=NODE_ID,
        workspace_projects_base=str(ws_root),
    )


def _inspect_running(*, version: int | None) -> ContainerInspectionResult:
    labels: tuple[tuple[str, str], ...] = ()
    if version is not None:
        labels = ((CONFIG_LABEL, str(version)),)
    return ContainerInspectionResult(
        exists=True,
        container_id=CONTAINER_ID,
        container_state="running",
        pid=1,
        ports=(),
        mounts=(),
        labels=labels,
    )


def _stop_ok(*, issues: list[str] | None = None) -> WorkspaceStopResult:
    return WorkspaceStopResult(
        workspace_id=WORKSPACE_ID,
        success=True,
        container_id=CONTAINER_ID,
        container_state="stopped",
        topology_detached=True,
        issues=issues,
    )


def _bringup_ok(*, issues: list[str] | None = None) -> WorkspaceBringUpResult:
    return WorkspaceBringUpResult(
        workspace_id=WORKSPACE_ID,
        success=True,
        node_id=NODE_ID,
        topology_id=TOPOLOGY_ID_BRINGUP,
        container_id=CONTAINER_ID,
        container_state="running",
        netns_ref=NETNS_REF,
        workspace_ip=WORKSPACE_IP,
        internal_endpoint=INTERNAL_ENDPOINT,
        probe_healthy=True,
        issues=issues,
    )


class TestUpdateNoop:
    def test_no_op_success_probe_only_no_stop_no_bringup(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        mock_runtime.inspect_container.return_value = _inspect_running(version=5)
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

        with patch.object(svc, "restart_workspace_runtime") as m_restart:
            with patch.object(svc, "stop_workspace_runtime") as m_stop:
                with patch.object(svc, "bring_up_workspace_runtime") as m_up:
                    out = svc.update_workspace_runtime(
                        workspace_id=WORKSPACE_ID,
                        requested_config_version=5,
                        requested_by="noop-auditor",
                    )

        m_restart.assert_not_called()
        m_stop.assert_not_called()
        m_up.assert_not_called()

        mock_runtime.inspect_container.assert_called_with(container_id=CONTAINER_REF)

        assert out.success is True
        assert out.no_op is True
        assert out.update_strategy == "noop"
        assert out.workspace_id == WORKSPACE_ID
        assert out.current_config_version == 5
        assert out.requested_config_version == 5
        assert out.container_id == CONTAINER_ID
        assert (out.container_state or "").lower() == "running"
        assert out.node_id == NODE_ID
        assert out.topology_id == str(SERVICE_TOPOLOGY_ID)
        assert out.workspace_ip == WORKSPACE_IP
        assert out.internal_endpoint == INTERNAL_ENDPOINT
        assert out.probe_healthy is True
        assert out.issues is None or out.issues == []

    def test_no_op_missing_container_current_zero(
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

        with patch.object(svc, "stop_workspace_runtime") as m_stop:
            with patch.object(svc, "bring_up_workspace_runtime") as m_up:
                out = svc.update_workspace_runtime(workspace_id=WORKSPACE_ID, requested_config_version=0)

        m_stop.assert_not_called()
        m_up.assert_not_called()

        assert out.success is False
        assert out.no_op is True
        assert out.update_strategy == "noop"
        assert out.issues and "not_found" in out.issues[0]


class TestUpdateRestartHappyPath:
    def test_restart_strategy_stop_then_bringup_full_result(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        """Real ``restart_workspace_runtime`` with patched stop/bring-up (version 1 → 2)."""
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        mock_runtime.inspect_container.return_value = _inspect_running(version=1)

        with patch.object(svc, "stop_workspace_runtime", return_value=_stop_ok(issues=[])) as m_stop:
            with patch.object(svc, "bring_up_workspace_runtime", return_value=_bringup_ok(issues=[])) as m_up:
                out = svc.update_workspace_runtime(
                    workspace_id=WORKSPACE_ID,
                    requested_config_version=2,
                    requested_by="update-op",
                )

        m_stop.assert_called_once_with(workspace_id=WORKSPACE_ID, container_id=None, requested_by="update-op")
        m_up.assert_called_once_with(workspace_id=WORKSPACE_ID, requested_config_version=2)

        assert out.success is True
        assert out.no_op is False
        assert out.update_strategy == "restart"
        assert out.current_config_version == 1
        assert out.requested_config_version == 2
        assert out.stop_success is True
        assert out.bringup_success is True
        assert out.container_id == CONTAINER_ID
        assert (out.container_state or "").lower() == "running"
        assert out.node_id == NODE_ID
        assert out.topology_id == TOPOLOGY_ID_BRINGUP
        assert out.workspace_ip == WORKSPACE_IP
        assert out.internal_endpoint == INTERNAL_ENDPOINT
        assert out.probe_healthy is True
        assert out.issues is None or out.issues == []


class TestUpdateStopFailure:
    def test_stop_unsuccessful_returns_failed_update_without_bringup(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        mock_runtime.inspect_container.return_value = _inspect_running(version=1)
        stop_ret = WorkspaceStopResult(
            workspace_id=WORKSPACE_ID,
            success=False,
            container_id=CONTAINER_ID,
            container_state="running",
            topology_detached=False,
            issues=["runtime:stop_failed:engine"],
        )

        with patch.object(svc, "stop_workspace_runtime", return_value=stop_ret) as m_stop:
            with patch.object(svc, "bring_up_workspace_runtime") as m_up:
                out = svc.update_workspace_runtime(workspace_id=WORKSPACE_ID, requested_config_version=2)

        m_stop.assert_called_once()
        m_up.assert_not_called()

        assert out.success is False
        assert out.no_op is False
        assert out.update_strategy == "restart"
        assert out.stop_success is False
        assert out.bringup_success is False
        assert out.issues == ["runtime:stop_failed:engine"]

    def test_restart_raises_workspace_restart_error_wrapped_as_update_error(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        mock_runtime.inspect_container.return_value = _inspect_running(version=1)
        with patch.object(
            svc,
            "restart_workspace_runtime",
            side_effect=WorkspaceRestartError("inspect_container failed: boom"),
        ):
            with pytest.raises(WorkspaceUpdateError, match="inspect_container failed"):
                svc.update_workspace_runtime(workspace_id=WORKSPACE_ID, requested_config_version=2)


class TestUpdateBringUpFailureAfterStop:
    def test_bringup_raises_workspace_bring_up_error(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        mock_runtime.inspect_container.return_value = _inspect_running(version=1)

        with patch.object(svc, "stop_workspace_runtime", return_value=_stop_ok()) as m_stop:
            with patch.object(
                svc,
                "bring_up_workspace_runtime",
                side_effect=WorkspaceBringUpError("topology bring-up failed: x"),
            ) as m_up:
                out = svc.update_workspace_runtime(workspace_id=WORKSPACE_ID, requested_config_version=2)

        m_stop.assert_called_once()
        m_up.assert_called_once()

        assert out.success is False
        assert out.stop_success is True
        assert out.bringup_success is False
        assert out.issues and out.issues[0].startswith("bringup:failed:")

    def test_bringup_returns_unhealthy_probe(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        mock_runtime.inspect_container.return_value = _inspect_running(version=1)
        up_ret = WorkspaceBringUpResult(
            workspace_id=WORKSPACE_ID,
            success=False,
            node_id=NODE_ID,
            topology_id=TOPOLOGY_ID_BRINGUP,
            container_id=CONTAINER_ID,
            container_state="running",
            workspace_ip=WORKSPACE_IP,
            internal_endpoint=INTERNAL_ENDPOINT,
            probe_healthy=False,
            issues=["probe:tcp:connection refused"],
        )

        with patch.object(svc, "stop_workspace_runtime", return_value=_stop_ok()) as m_stop:
            with patch.object(svc, "bring_up_workspace_runtime", return_value=up_ret) as m_up:
                out = svc.update_workspace_runtime(workspace_id=WORKSPACE_ID, requested_config_version=2)

        m_stop.assert_called_once()
        m_up.assert_called_once()

        assert out.success is False
        assert out.stop_success is True
        assert out.bringup_success is False
        assert out.probe_healthy is False
        assert "probe:tcp:connection refused" in (out.issues or [])


class TestUpdateCallOrder:
    def test_stop_invoked_before_bring_up_inside_restart(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        mock_runtime.inspect_container.return_value = _inspect_running(version=1)
        order: list[str] = []

        def _stop(**kwargs: object) -> WorkspaceStopResult:
            order.append("stop")
            return _stop_ok()

        def _bringup(**kwargs: object) -> WorkspaceBringUpResult:
            order.append("bringup")
            return _bringup_ok()

        with patch.object(svc, "stop_workspace_runtime", side_effect=_stop):
            with patch.object(svc, "bring_up_workspace_runtime", side_effect=_bringup):
                svc.update_workspace_runtime(workspace_id=WORKSPACE_ID, requested_config_version=2)

        assert order == ["stop", "bringup"]


class TestUpdatePassthrough:
    def test_requested_config_version_forwarded_to_bring_up(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        mock_runtime.inspect_container.return_value = _inspect_running(version=0)
        with patch.object(svc, "stop_workspace_runtime", return_value=_stop_ok()):
            with patch.object(svc, "bring_up_workspace_runtime", return_value=_bringup_ok()) as m_up:
                svc.update_workspace_runtime(workspace_id=WORKSPACE_ID, requested_config_version=42)
        m_up.assert_called_once_with(workspace_id=WORKSPACE_ID, requested_config_version=42)

    def test_requested_by_forwarded_to_stop_via_restart(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        mock_runtime.inspect_container.return_value = _inspect_running(version=1)
        with patch.object(svc, "stop_workspace_runtime", return_value=_stop_ok()) as m_stop:
            with patch.object(svc, "bring_up_workspace_runtime", return_value=_bringup_ok()) as m_up:
                svc.update_workspace_runtime(
                    workspace_id=WORKSPACE_ID,
                    requested_config_version=3,
                    requested_by="audit-subject",
                )
        m_stop.assert_called_once_with(workspace_id=WORKSPACE_ID, container_id=None, requested_by="audit-subject")
        m_up.assert_called_once_with(workspace_id=WORKSPACE_ID, requested_config_version=3)


class TestUpdateValidation:
    def test_negative_version_raises(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceUpdateError, match="non-negative"):
            svc.update_workspace_runtime(workspace_id=WORKSPACE_ID, requested_config_version=-1)

    def test_empty_workspace_id_raises(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceUpdateError, match="empty"):
            svc.update_workspace_runtime(workspace_id="   ", requested_config_version=0)


class TestUpdateRestartDelegationShortcut:
    def test_version_mismatch_can_patch_restart_only(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        """Smoke: update maps ``WorkspaceRestartResult`` fields when ``restart`` is mocked."""
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        mock_runtime.inspect_container.return_value = _inspect_running(version=1)
        rrestart = WorkspaceRestartResult(
            workspace_id=WORKSPACE_ID,
            success=True,
            stop_success=True,
            bringup_success=True,
            container_id=CONTAINER_ID,
            container_state="running",
            node_id=NODE_ID,
            topology_id=str(SERVICE_TOPOLOGY_ID),
            workspace_ip=WORKSPACE_IP,
            internal_endpoint=INTERNAL_ENDPOINT,
            probe_healthy=True,
        )
        with patch.object(svc, "restart_workspace_runtime", return_value=rrestart) as m_r:
            out = svc.update_workspace_runtime(workspace_id=WORKSPACE_ID, requested_config_version=2)

        m_r.assert_called_once_with(
            workspace_id=WORKSPACE_ID,
            container_id=None,
            requested_by=None,
            requested_config_version=2,
        )
        assert out.update_strategy == "restart"
        assert out.current_config_version == 1
