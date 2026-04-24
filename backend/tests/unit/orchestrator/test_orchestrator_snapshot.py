"""Unit tests: DefaultOrchestratorService snapshot export/import (local tar.gz)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.libs.probes.interfaces import ProbeRunner
from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.topology.interfaces import TopologyAdapter
from app.services.node_execution_service.workspace_project_dir import WORKSPACE_USER_PROJECT_SUBDIR
from app.services.orchestrator_service import DefaultOrchestratorService

WORKSPACE_ID = "55"
NODE_ID = "node-a"
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


def _make_service(ws_root: Path, mock_runtime, mock_topology, mock_probe) -> DefaultOrchestratorService:
    return DefaultOrchestratorService(
        mock_runtime,
        mock_topology,
        mock_probe,
        topology_id=TOPOLOGY_ID,
        node_id=NODE_ID,
        workspace_projects_base=str(ws_root),
    )


def test_export_import_roundtrip(
    tmp_path: Path,
    mock_runtime: MagicMock,
    mock_topology: MagicMock,
    mock_probe: MagicMock,
) -> None:
    ws_root = tmp_path / "wsroot"
    proj = ws_root / WORKSPACE_ID
    proj.mkdir(parents=True)
    (proj / "hello.txt").write_text("snapshot-data", encoding="utf-8")

    svc = _make_service(ws_root, mock_runtime, mock_topology, mock_probe)
    archive = tmp_path / "snap.tar.gz"
    exp = svc.export_workspace_filesystem_snapshot(workspace_id=WORKSPACE_ID, archive_path=str(archive))
    assert exp.success is True
    assert exp.size_bytes and exp.size_bytes > 0

    (proj / "hello.txt").unlink()
    imp = svc.import_workspace_filesystem_snapshot(workspace_id=WORKSPACE_ID, archive_path=str(archive))
    assert imp.success is True
    assert (proj / "hello.txt").read_text(encoding="utf-8") == "snapshot-data"


def test_export_import_roundtrip_uses_project_subdir_when_present(
    tmp_path: Path,
    mock_runtime: MagicMock,
    mock_topology: MagicMock,
    mock_probe: MagicMock,
) -> None:
    """v2 layout: snapshots archive only ``project/``, not sibling ``code-server/``."""
    ws_root = tmp_path / "wsroot"
    bundle = ws_root / WORKSPACE_ID
    proj = bundle / WORKSPACE_USER_PROJECT_SUBDIR
    proj.mkdir(parents=True)
    (proj / "tracked.txt").write_text("in-project", encoding="utf-8")
    cs_noise = bundle / "code-server" / "data" / "User" / "noise.txt"
    cs_noise.parent.mkdir(parents=True)
    cs_noise.write_text("internal", encoding="utf-8")

    svc = _make_service(ws_root, mock_runtime, mock_topology, mock_probe)
    archive = tmp_path / "snap.tar.gz"
    exp = svc.export_workspace_filesystem_snapshot(workspace_id=WORKSPACE_ID, archive_path=str(archive))
    assert exp.success is True

    (proj / "tracked.txt").unlink()
    imp = svc.import_workspace_filesystem_snapshot(workspace_id=WORKSPACE_ID, archive_path=str(archive))
    assert imp.success is True
    assert (proj / "tracked.txt").read_text(encoding="utf-8") == "in-project"
    assert cs_noise.read_text(encoding="utf-8") == "internal"
