"""Unit tests: ``DefaultOrchestratorService.stop_workspace_runtime`` (mocked deps)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.libs.probes.interfaces import ProbeRunner
from app.libs.runtime.errors import ContainerStopError
from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.runtime.models import ContainerInspectionResult, RuntimeActionResult
from app.libs.topology.errors import WorkspaceDetachError
from app.libs.topology.interfaces import TopologyAdapter
from app.libs.topology.models.enums import TopologyAttachmentStatus
from app.libs.topology.results import DetachWorkspaceResult
from app.services.orchestrator_service import DefaultOrchestratorService
from app.services.orchestrator_service.errors import WorkspaceBringUpError, WorkspaceStopError

# V1 topology parsing requires a non-negative base-10 integer string (same as bring-up tests).
WORKSPACE_ID = "123"
CONTAINER_REF = f"devnest-ws-{WORKSPACE_ID}"
CONTAINER_ID = "container-abc"
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


def _inspect_running(mock_runtime: MagicMock) -> None:
    mock_runtime.inspect_container.return_value = ContainerInspectionResult(
        exists=True,
        container_id=CONTAINER_ID,
        container_state="running",
        pid=4242,
        ports=(),
        mounts=(),
    )


def _detach_ok(mock_topology: MagicMock) -> None:
    mock_topology.detach_workspace.return_value = DetachWorkspaceResult(
        detached=True,
        status=TopologyAttachmentStatus.DETACHED,
        workspace_id=int(WORKSPACE_ID),
        workspace_ip="10.0.0.5",
        released_ip=False,
    )


def _stop_ok(mock_runtime: MagicMock, *, state: str = "stopped") -> None:
    mock_runtime.stop_container.return_value = RuntimeActionResult(
        container_id=CONTAINER_ID,
        container_state=state,
        success=True,
        message=None,
    )


class TestStopHappyPath:
    def test_detach_then_stop_success(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _inspect_running(mock_runtime)
        _detach_ok(mock_topology)
        _stop_ok(mock_runtime)

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        out = svc.stop_workspace_runtime(workspace_id=WORKSPACE_ID)

        assert out.success is True
        assert out.workspace_id == WORKSPACE_ID
        assert out.container_id == CONTAINER_ID
        assert out.container_state == "stopped"
        assert out.topology_detached is True
        assert out.issues is None or out.issues == []

        mock_runtime.inspect_container.assert_called_once_with(container_id=CONTAINER_REF)
        mock_topology.detach_workspace.assert_called_once_with(
            topology_id=TOPOLOGY_ID,
            node_id=NODE_ID,
            workspace_id=int(WORKSPACE_ID),
        )
        mock_runtime.stop_container.assert_called_once_with(container_id=CONTAINER_ID)

    def test_call_order_detach_before_stop(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        order: list[str] = []

        def _detach_side_effect(*_a: object, **_kw: object) -> DetachWorkspaceResult:
            order.append("detach")
            return DetachWorkspaceResult(
                detached=True,
                status=TopologyAttachmentStatus.DETACHED,
                workspace_id=int(WORKSPACE_ID),
                workspace_ip=None,
                released_ip=False,
            )

        def _stop_side_effect(*_a: object, **_kw: object) -> RuntimeActionResult:
            order.append("stop")
            return RuntimeActionResult(
                container_id=CONTAINER_ID,
                container_state="stopped",
                success=True,
                message=None,
            )

        _inspect_running(mock_runtime)
        mock_topology.detach_workspace.side_effect = _detach_side_effect
        mock_runtime.stop_container.side_effect = _stop_side_effect

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        svc.stop_workspace_runtime(workspace_id=WORKSPACE_ID)

        assert order == ["detach", "stop"]


class TestStopUnexpectedFailuresRaise:
    """``WorkspaceStopError`` is raised for unexpected (non-``TopologyError``) detach/stop failures."""

    def test_unexpected_detach_exception_raises_and_skips_stop(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _inspect_running(mock_runtime)
        mock_topology.detach_workspace.side_effect = ValueError("detach boom")

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceStopError, match="unexpected detach failure"):
            svc.stop_workspace_runtime(workspace_id=WORKSPACE_ID)

        mock_runtime.stop_container.assert_not_called()

    def test_unexpected_stop_exception_raises_after_detach(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _inspect_running(mock_runtime)
        _detach_ok(mock_topology)
        mock_runtime.stop_container.side_effect = ValueError("stop boom")

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceStopError, match="unexpected stop failure"):
            svc.stop_workspace_runtime(workspace_id=WORKSPACE_ID)

        mock_topology.detach_workspace.assert_called_once()


class TestStopRuntimeAdapterErrorsBecomeIssues:
    """``RuntimeAdapterError`` from ``stop_container`` is recorded; no ``WorkspaceStopError``."""

    def test_runtime_stop_error_recorded(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _inspect_running(mock_runtime)
        _detach_ok(mock_topology)
        mock_runtime.stop_container.side_effect = ContainerStopError("engine stop failed")

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        out = svc.stop_workspace_runtime(workspace_id=WORKSPACE_ID)

        assert out.success is False
        assert out.topology_detached is True
        assert out.issues
        assert any("runtime:stop_failed" in msg for msg in out.issues)
        mock_topology.detach_workspace.assert_called_once()
        mock_runtime.stop_container.assert_called_once_with(container_id=CONTAINER_ID)


class TestStopTopologyDetachTopologyErrorBestEffort:
    """Current implementation: ``TopologyError`` on detach is swallowed; stop still runs."""

    def test_topology_detach_error_still_calls_stop_and_marks_failure(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _inspect_running(mock_runtime)
        mock_topology.detach_workspace.side_effect = WorkspaceDetachError("persist failed")
        _stop_ok(mock_runtime)

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        out = svc.stop_workspace_runtime(workspace_id=WORKSPACE_ID)

        assert out.success is False
        assert out.topology_detached is False
        assert out.issues and any("topology:detach_failed" in msg for msg in out.issues)
        mock_runtime.stop_container.assert_called_once_with(container_id=CONTAINER_ID)


class TestStopContainerAlreadyStopped:
    def test_inspect_exited_and_stop_success_returns_ok(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        mock_runtime.inspect_container.return_value = ContainerInspectionResult(
            exists=True,
            container_id=CONTAINER_ID,
            container_state="exited",
            pid=None,
            ports=(),
            mounts=(),
        )
        _detach_ok(mock_topology)
        _stop_ok(mock_runtime, state="exited")

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        out = svc.stop_workspace_runtime(workspace_id=WORKSPACE_ID)

        assert out.success is True
        assert out.container_state == "exited"
        assert out.topology_detached is True
        assert out.issues is None or out.issues == []


class TestStopValidation:
    def test_empty_workspace_id_raises_workspace_stop_error(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceStopError, match="workspace_id is empty"):
            svc.stop_workspace_runtime(workspace_id="  ")

    def test_non_integer_workspace_id_raises_workspace_bring_up_error(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        """Reuses bring-up parser today (``WorkspaceBringUpError``)."""
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceBringUpError, match="base-10 integer"):
            svc.stop_workspace_runtime(workspace_id="ws-123")
