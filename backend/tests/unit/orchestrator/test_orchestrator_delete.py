"""Unit tests: ``DefaultOrchestratorService.delete_workspace_runtime`` (mocked deps)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.libs.probes.interfaces import ProbeRunner
from app.libs.runtime.errors import ContainerDeleteError
from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.runtime.models import ContainerInspectionResult, RuntimeActionResult
from app.libs.topology.errors import TopologyDeleteError, WorkspaceDetachError
from app.libs.topology.interfaces import TopologyAdapter
from app.libs.topology.models.enums import TopologyAttachmentStatus
from app.libs.topology.results import DetachWorkspaceResult
from app.services.orchestrator_service import DefaultOrchestratorService
from app.services.orchestrator_service.errors import WorkspaceDeleteError

# V1 topology parsing requires a non-negative base-10 integer string.
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


def _inspect_missing(mock_runtime: MagicMock) -> None:
    mock_runtime.inspect_container.return_value = ContainerInspectionResult(
        exists=False,
        container_id=None,
        container_state="missing",
        pid=None,
        ports=(),
        mounts=(),
    )


def _detach_ok(mock_topology: MagicMock, *, detached: bool = True) -> None:
    mock_topology.detach_workspace.return_value = DetachWorkspaceResult(
        detached=detached,
        status=TopologyAttachmentStatus.DETACHED,
        workspace_id=int(WORKSPACE_ID),
        workspace_ip="10.0.0.5",
        released_ip=False,
    )


def _delete_ok(mock_runtime: MagicMock, *, state: str = "missing", success: bool = True) -> None:
    mock_runtime.delete_container.return_value = RuntimeActionResult(
        container_id=CONTAINER_ID,
        container_state=state,
        success=success,
        message=None if success else "delete failed",
    )


class TestDeleteHappyPath:
    def test_detach_then_delete_then_optional_topology_delete_success(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        order: list[str] = []

        _inspect_running(mock_runtime)

        def _detach_side_effect(*_a: object, **_kw: object) -> DetachWorkspaceResult:
            order.append("detach")
            return DetachWorkspaceResult(
                detached=True,
                status=TopologyAttachmentStatus.DETACHED,
                workspace_id=int(WORKSPACE_ID),
                workspace_ip="10.0.0.5",
                released_ip=False,
            )

        def _delete_side_effect(*_a: object, **_kw: object) -> RuntimeActionResult:
            order.append("delete_container")
            return RuntimeActionResult(
                container_id=CONTAINER_ID,
                container_state="missing",
                success=True,
                message=None,
            )

        def _delete_topology_side_effect(*_a: object, **_kw: object) -> None:
            order.append("delete_topology")

        mock_topology.detach_workspace.side_effect = _detach_side_effect
        mock_runtime.delete_container.side_effect = _delete_side_effect
        mock_topology.delete_topology.side_effect = _delete_topology_side_effect

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        out = svc.delete_workspace_runtime(workspace_id=WORKSPACE_ID)

        assert out.success is True
        assert out.workspace_id == WORKSPACE_ID
        assert out.container_deleted is True
        assert out.topology_detached is True
        assert out.topology_deleted is True
        assert out.container_id == CONTAINER_ID
        assert out.issues is None or out.issues == []
        assert order == ["detach", "delete_container", "delete_topology"]

        mock_runtime.inspect_container.assert_called_once_with(container_id=CONTAINER_REF)
        mock_topology.detach_workspace.assert_called_once_with(
            topology_id=TOPOLOGY_ID,
            node_id=NODE_ID,
            workspace_id=int(WORKSPACE_ID),
        )
        mock_runtime.delete_container.assert_called_once_with(container_id=CONTAINER_ID)
        mock_topology.delete_topology.assert_called_once_with(
            topology_id=TOPOLOGY_ID,
            node_id=NODE_ID,
        )


class TestDeleteContainerMissing:
    def test_container_already_deleted_is_handled_gracefully(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _inspect_missing(mock_runtime)
        _detach_ok(mock_topology)
        _delete_ok(mock_runtime, state="missing", success=True)

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        out = svc.delete_workspace_runtime(workspace_id=WORKSPACE_ID)

        # Current implementation: delete_container(success=True) means container_deleted=True.
        assert out.success is True
        assert out.container_deleted is True
        assert out.topology_detached is True
        assert out.issues is None or out.issues == []
        mock_runtime.delete_container.assert_called_once()


class TestDeleteUnexpectedFailuresRaise:
    """WorkspaceDeleteError is reserved for unexpected (non-adapter) failures."""

    def test_unexpected_detach_exception_raises_and_skips_runtime_delete(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _inspect_running(mock_runtime)
        mock_topology.detach_workspace.side_effect = ValueError("detach boom")

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceDeleteError, match="unexpected detach failure"):
            svc.delete_workspace_runtime(workspace_id=WORKSPACE_ID)

        mock_runtime.delete_container.assert_not_called()
        mock_topology.delete_topology.assert_not_called()

    def test_unexpected_runtime_delete_exception_raises_after_detach(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _inspect_running(mock_runtime)
        _detach_ok(mock_topology)
        mock_runtime.delete_container.side_effect = ValueError("delete boom")

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceDeleteError, match="unexpected delete failure"):
            svc.delete_workspace_runtime(workspace_id=WORKSPACE_ID)

        mock_topology.detach_workspace.assert_called_once()
        mock_topology.delete_topology.assert_not_called()


class TestDeleteAdapterErrorsBecomeIssues:
    """Current semantics: adapter-specific errors are recorded in result issues."""

    def test_topology_detach_error_records_issue_and_continues(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _inspect_running(mock_runtime)
        mock_topology.detach_workspace.side_effect = WorkspaceDetachError("persist failed")
        _delete_ok(mock_runtime, success=True)

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        out = svc.delete_workspace_runtime(workspace_id=WORKSPACE_ID)

        assert out.success is False
        assert out.container_deleted is True
        assert out.topology_detached is False
        assert out.issues and any("topology:detach_failed" in msg for msg in out.issues)
        mock_runtime.delete_container.assert_called_once_with(container_id=CONTAINER_ID)
        mock_topology.delete_topology.assert_called_once()

    def test_runtime_delete_error_records_issue_and_still_attempts_topology_delete(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _inspect_running(mock_runtime)
        _detach_ok(mock_topology)
        mock_runtime.delete_container.side_effect = ContainerDeleteError("engine delete failed")

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        out = svc.delete_workspace_runtime(workspace_id=WORKSPACE_ID)

        assert out.success is False
        assert out.container_deleted is False
        assert out.topology_detached is True
        assert out.issues and any("runtime:delete_failed" in msg for msg in out.issues)
        mock_topology.delete_topology.assert_called_once()

    def test_topology_delete_failure_is_recorded_not_raised(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        _inspect_running(mock_runtime)
        _detach_ok(mock_topology)
        _delete_ok(mock_runtime, success=True)
        mock_topology.delete_topology.side_effect = TopologyDeleteError("non-DETACHED attachments remain")

        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        out = svc.delete_workspace_runtime(workspace_id=WORKSPACE_ID)

        assert out.success is True
        assert out.container_deleted is True
        assert out.topology_detached is True
        assert out.topology_deleted is False
        assert out.issues and any("topology:delete_failed" in msg for msg in out.issues)


class TestDeleteValidation:
    def test_empty_workspace_id_raises_workspace_delete_error(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceDeleteError, match="workspace_id is empty"):
            svc.delete_workspace_runtime(workspace_id=" ")

    def test_non_integer_workspace_id_raises_workspace_delete_error(
        self,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
        mock_probe: MagicMock,
        ws_root: Path,
    ) -> None:
        svc = _make_service(mock_runtime, mock_topology, mock_probe, ws_root)
        with pytest.raises(WorkspaceDeleteError, match="base-10 integer"):
            svc.delete_workspace_runtime(workspace_id="ws-123")
