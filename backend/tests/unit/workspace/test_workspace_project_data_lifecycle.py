"""Tests for workspace project disk lifecycle fields on GET /workspaces/{id} detail."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlmodel import Session

from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceSnapshot,
    WorkspaceSnapshotStatus,
    WorkspaceStatus,
)
from app.services.workspace_service.services import workspace_intent_service
from app.services.node_execution_service.workspace_project_dir import (
    workspace_bundle_project_data_present,
    workspace_project_dir_name,
)


def test_workspace_bundle_project_data_present_v2(tmp_path: Path) -> None:
    b = tmp_path / "b1"
    b.mkdir()
    (b / "project").mkdir()
    assert workspace_bundle_project_data_present(b) is True


def test_workspace_bundle_project_data_present_legacy(tmp_path: Path) -> None:
    b = tmp_path / "b2"
    b.mkdir()
    (b / "app.py").write_text("x", encoding="utf-8")
    assert workspace_bundle_project_data_present(b) is True


def test_workspace_bundle_project_data_absent_code_server_only(tmp_path: Path) -> None:
    b = tmp_path / "b3"
    b.mkdir()
    (b / "code-server").mkdir()
    assert workspace_bundle_project_data_present(b) is False


def _seed_stopped_workspace(session: Session, owner_user_id: int) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="Disk WS",
        description="d",
        owner_user_id=owner_user_id,
        status=WorkspaceStatus.STOPPED.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.flush()
    session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={"k": 1}))
    session.commit()
    session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


@pytest.fixture
def mock_projects_base(tmp_path: Path):
    root = tmp_path / "workspace-projects-base"
    root.mkdir(parents=True)

    class _Settings:
        workspace_projects_base = str(root)
        devnest_gateway_enabled = False
        devnest_base_domain = "unit.test"

    with patch("app.libs.common.config.get_settings", return_value=_Settings()):
        yield root


def test_get_workspace_project_data_unrecoverable(
    workspace_unit_engine,
    owner_user_id: int,
    mock_projects_base: Path,
) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_stopped_workspace(session, owner_user_id)

    with Session(workspace_unit_engine) as session:
        detail = workspace_intent_service.get_workspace(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
        )
    assert detail is not None
    assert detail.project_data_lifecycle == "unrecoverable"
    assert detail.restorable_snapshot_count == 0
    assert detail.project_data_user_message
    assert any("Persisted project data" in msg for msg in detail.reopen_issues)


def test_get_workspace_project_data_ok_when_project_dir_exists(
    workspace_unit_engine,
    owner_user_id: int,
    mock_projects_base: Path,
) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_stopped_workspace(session, owner_user_id)
        ws = session.get(Workspace, wid)
        assert ws is not None
        key = (ws.project_storage_key or "").strip()
        dirname = workspace_project_dir_name(str(wid), key or None)
        bundle = mock_projects_base / dirname
        bundle.mkdir(parents=True)
        (bundle / "project").mkdir()

    with Session(workspace_unit_engine) as session:
        detail = workspace_intent_service.get_workspace(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
        )
    assert detail is not None
    assert detail.project_data_lifecycle == "ok"
    assert detail.project_data_user_message is None


def test_get_workspace_project_data_restore_required_when_snapshot_exists(
    workspace_unit_engine,
    owner_user_id: int,
    mock_projects_base: Path,
) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_stopped_workspace(session, owner_user_id)
        snap = WorkspaceSnapshot(
            workspace_id=wid,
            name="snap",
            storage_uri="file:///tmp/x.tar.gz",
            status=WorkspaceSnapshotStatus.AVAILABLE.value,
            created_by_user_id=owner_user_id,
        )
        session.add(snap)
        session.commit()

    with Session(workspace_unit_engine) as session:
        detail = workspace_intent_service.get_workspace(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
        )
    assert detail is not None
    assert detail.project_data_lifecycle == "restore_required"
    assert detail.restorable_snapshot_count >= 1
    assert "snapshot" in (detail.project_data_user_message or "").lower()
