"""Unit tests: ``DefaultOrchestratorService.restart_workspace_runtime`` (mocked stop + bring-up).

V1 ``workspace_id`` must be a non-negative base-10 integer string (same as bring-up/stop/delete tests).
The prompt example ``\"ws-123\"`` is not valid for ``_parse_topology_workspace_id``; we use ``\"123\"`` as
the logical workspace key and keep container/IP values aligned with the requested fixtures.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.libs.probes.interfaces import ProbeRunner
from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.topology.interfaces import TopologyAdapter
from app.services.orchestrator_service import DefaultOrchestratorService
from app.services.orchestrator_service.errors import WorkspaceBringUpError, WorkspaceRestartError, WorkspaceStopError
from app.services.orchestrator_service.results import WorkspaceBringUpResult, WorkspaceStopResult

# Logical workspace id (V1 integer string).
WORKSPACE_ID = "123"
CONTAINER_ID_STOP_PHASE = "container-abc"
CONTAINER_ID_RUNNING = "container-abc"
NODE_ID = "node-1"
# Injected placement on the service (int); bring-up mock can still return a display topology id string.
SERVICE_TOPOLOGY_ID = 1
TOPOLOGY_ID_BRINGUP = "topo-1"
WORKSPACE_IP = "10.128.0.10"
INTERNAL_ENDPOINT = "10.128.0.10:8080"
NETNS_REF = "/proc/12345/ns/net"


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


def _stop_ok_result(*, issues: list[str] | None = None) -> WorkspaceStopResult:
    return WorkspaceStopResult(
        workspace_id=WORKSPACE_ID,
        success=True,
        container_id=CONTAINER_ID_STOP_PHASE,
        container_state="stopped",
        topology_detached=True,
        issues=issues,
    )


def _bringup_ok_result(*, issues: list[str] | None = None) -> WorkspaceBringUpResult:
    return WorkspaceBringUpResult(
        workspace_id=WORKSPACE_ID,
        success=True,
        node_id=NODE_ID,
        topology_id=TOPOLOGY_ID_BRINGUP,
        container_id=CONTAINER_ID_RUNNING,
        container_state="running",
        netns_ref=NETNS_REF,
        workspace_ip=WORKSPACE_IP,
        internal_endpoint=INTERNAL_ENDPOINT,
        probe_healthy=True,
        issues=issues,
    )


class TestRestartHappyPath:
    def test_stop_then_bringup_success_full_result(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        stop_ret = _stop_ok_result(issues=[])
        up_ret = _bringup_ok_result(issues=[])

        with patch.object(svc, "stop_workspace_runtime", return_value=stop_ret) as m_stop:
            with patch.object(svc, "bring_up_workspace_runtime", return_value=up_ret) as m_up:
                out = svc.restart_workspace_runtime(
                    workspace_id=WORKSPACE_ID,
                    requested_by="operator-1",
                    requested_config_version=99,
                )

        m_stop.assert_called_once_with(workspace_id=WORKSPACE_ID, requested_by="operator-1")
        m_up.assert_called_once_with(workspace_id=WORKSPACE_ID, requested_config_version=99)

        assert out.success is True
        assert out.workspace_id == WORKSPACE_ID
        assert out.stop_success is True
        assert out.bringup_success is True
        assert out.container_id == CONTAINER_ID_RUNNING
        assert out.container_state == "running"
        assert out.node_id == NODE_ID
        assert out.topology_id == TOPOLOGY_ID_BRINGUP
        assert out.workspace_ip == WORKSPACE_IP
        assert out.internal_endpoint == INTERNAL_ENDPOINT
        assert out.probe_healthy is True
        assert out.issues is None or out.issues == []


class TestRestartStopFailure:
    def test_unsuccessful_stop_returns_failed_result_without_bringup(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        """Current semantics: ``success=False`` stop result → ``WorkspaceRestartResult``, no exception."""
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        stop_ret = WorkspaceStopResult(
            workspace_id=WORKSPACE_ID,
            success=False,
            container_id=CONTAINER_ID_STOP_PHASE,
            container_state="running",
            topology_detached=False,
            issues=["runtime:stop_failed:engine"],
        )

        with patch.object(svc, "stop_workspace_runtime", return_value=stop_ret) as m_stop:
            with patch.object(svc, "bring_up_workspace_runtime") as m_up:
                out = svc.restart_workspace_runtime(workspace_id=WORKSPACE_ID)

        m_stop.assert_called_once()
        m_up.assert_not_called()

        assert out.success is False
        assert out.stop_success is False
        assert out.bringup_success is False
        assert out.container_id == CONTAINER_ID_STOP_PHASE
        assert out.container_state == "running"
        assert out.node_id == NODE_ID
        assert out.topology_id == str(SERVICE_TOPOLOGY_ID)
        assert out.workspace_ip is None
        assert out.internal_endpoint is None
        assert out.probe_healthy is None
        assert out.issues == ["runtime:stop_failed:engine"]

    def test_stop_raises_workspace_stop_error_as_workspace_restart_error(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with patch.object(
            svc,
            "stop_workspace_runtime",
            side_effect=WorkspaceStopError("inspect_container failed: boom"),
        ):
            with patch.object(svc, "bring_up_workspace_runtime") as m_up:
                with pytest.raises(WorkspaceRestartError, match="inspect_container failed"):
                    svc.restart_workspace_runtime(workspace_id=WORKSPACE_ID)
        m_up.assert_not_called()


class TestRestartBringUpFailureAfterStop:
    def test_bringup_raises_workspace_bring_up_error(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        stop_ret = _stop_ok_result()

        with patch.object(svc, "stop_workspace_runtime", return_value=stop_ret) as m_stop:
            with patch.object(
                svc,
                "bring_up_workspace_runtime",
                side_effect=WorkspaceBringUpError("topology bring-up failed: x"),
            ) as m_up:
                out = svc.restart_workspace_runtime(workspace_id=WORKSPACE_ID)

        m_stop.assert_called_once()
        m_up.assert_called_once()

        assert out.success is False
        assert out.stop_success is True
        assert out.bringup_success is False
        assert out.container_id == CONTAINER_ID_STOP_PHASE
        assert out.container_state == "stopped"
        assert out.issues and out.issues[0].startswith("bringup:failed:")
        assert "topology bring-up failed" in (out.issues[0] if out.issues else "")

    def test_bringup_returns_unsuccessful_probe(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        stop_ret = _stop_ok_result()
        up_ret = WorkspaceBringUpResult(
            workspace_id=WORKSPACE_ID,
            success=False,
            node_id=NODE_ID,
            topology_id=TOPOLOGY_ID_BRINGUP,
            container_id=CONTAINER_ID_RUNNING,
            container_state="running",
            workspace_ip=WORKSPACE_IP,
            internal_endpoint=INTERNAL_ENDPOINT,
            probe_healthy=False,
            issues=["probe:tcp:connection refused"],
        )

        with patch.object(svc, "stop_workspace_runtime", return_value=stop_ret) as m_stop:
            with patch.object(svc, "bring_up_workspace_runtime", return_value=up_ret) as m_bring:
                out = svc.restart_workspace_runtime(workspace_id=WORKSPACE_ID)

        m_stop.assert_called_once()
        m_bring.assert_called_once()

        assert out.success is False
        assert out.stop_success is True
        assert out.bringup_success is False
        assert out.probe_healthy is False
        assert "probe:tcp:connection refused" in (out.issues or [])


class TestRestartCallOrder:
    def test_stop_is_invoked_before_bring_up(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        order: list[str] = []

        def _stop(**kwargs: object) -> WorkspaceStopResult:
            order.append("stop")
            return _stop_ok_result()

        def _bringup(**kwargs: object) -> WorkspaceBringUpResult:
            order.append("bringup")
            return _bringup_ok_result()

        with patch.object(svc, "stop_workspace_runtime", side_effect=_stop) as m_stop:
            with patch.object(svc, "bring_up_workspace_runtime", side_effect=_bringup):
                svc.restart_workspace_runtime(workspace_id=WORKSPACE_ID)

        m_stop.assert_called_once()
        assert order == ["stop", "bringup"]


class TestRestartPassthrough:
    def test_requested_config_version_forwarded_to_bring_up_only(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with patch.object(svc, "stop_workspace_runtime", return_value=_stop_ok_result()) as m_stop:
            with patch.object(svc, "bring_up_workspace_runtime", return_value=_bringup_ok_result()) as m_up:
                svc.restart_workspace_runtime(
                    workspace_id=WORKSPACE_ID,
                    requested_config_version=42,
                )
        m_stop.assert_called_once_with(workspace_id=WORKSPACE_ID, requested_by=None)
        m_up.assert_called_once_with(workspace_id=WORKSPACE_ID, requested_config_version=42)

    def test_requested_config_version_none_passthrough(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with patch.object(svc, "stop_workspace_runtime", return_value=_stop_ok_result()):
            with patch.object(svc, "bring_up_workspace_runtime", return_value=_bringup_ok_result()) as m_up:
                svc.restart_workspace_runtime(workspace_id=WORKSPACE_ID, requested_config_version=None)
        m_up.assert_called_once_with(workspace_id=WORKSPACE_ID, requested_config_version=None)

    def test_requested_by_forwarded_to_stop_only(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with patch.object(svc, "stop_workspace_runtime", return_value=_stop_ok_result()) as m_stop:
            with patch.object(svc, "bring_up_workspace_runtime", return_value=_bringup_ok_result()) as m_up:
                svc.restart_workspace_runtime(
                    workspace_id=WORKSPACE_ID,
                    requested_by="audit-subject",
                    requested_config_version=3,
                )
        m_stop.assert_called_once_with(workspace_id=WORKSPACE_ID, requested_by="audit-subject")
        m_up.assert_called_once_with(workspace_id=WORKSPACE_ID, requested_config_version=3)


class TestRestartValidation:
    def test_empty_workspace_id_raises_workspace_restart_error(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceRestartError, match="empty"):
            svc.restart_workspace_runtime(workspace_id="   ")

    def test_invalid_workspace_id_raises_workspace_restart_error(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceRestartError, match="base-10 integer"):
            svc.restart_workspace_runtime(workspace_id="not-an-int")
