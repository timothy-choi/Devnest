"""Integration tests: snapshot jobs on PostgreSQL (mock orchestrator export/import)."""

from __future__ import annotations

import io
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import create_autospec

import pytest
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.services.auth_service.models import UserAuth
from app.services.orchestrator_service.interfaces import OrchestratorService
from app.services.orchestrator_service.results import WorkspaceSnapshotOperationResult
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceEvent,
    WorkspaceJob,
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceRuntime,
    WorkspaceRuntimeHealthStatus,
    WorkspaceSnapshot,
    WorkspaceStatus,
)
from app.services.workspace_service.models.enums import WorkspaceSnapshotStatus
from app.services.workspace_service.services import snapshot_service
from app.services.workspace_service.services.workspace_event_service import WorkspaceStreamEventType
from app.workers.workspace_job_worker.worker import run_pending_jobs

pytestmark = pytest.mark.integration

NODE_ID = "node-snap-1"


def _export_writes_minimal_archive(
    *,
    workspace_id: str,
    project_storage_key: str | None = None,
    archive_path: str,
    container_id: str | None = None,
) -> WorkspaceSnapshotOperationResult:
    """Autospec export mock must materialize a file: restore job checks ``os.path.isfile``."""
    _ = project_storage_key, container_id
    p = Path(archive_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(p, "w:gz") as tf:
        data = b"restore-test-payload\n"
        ti = tarfile.TarInfo(name="restored.txt")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    return WorkspaceSnapshotOperationResult(
        workspace_id=workspace_id,
        success=True,
        size_bytes=int(p.stat().st_size),
        issues=None,
    )


def _seed_owner(session: Session) -> int:
    user = UserAuth(
        username=f"snap_int_{datetime.now(timezone.utc).timestamp()}",
        email=f"snap_int_{datetime.now(timezone.utc).timestamp()}@example.com",
        password_hash="x",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.user_auth_id is not None
    return user.user_auth_id


def _seed_running_workspace(session: Session, owner: int) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="snap-ws",
        description="",
        owner_user_id=owner,
        status=WorkspaceStatus.RUNNING.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.flush()
    session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}))
    session.add(
        WorkspaceRuntime(
            workspace_id=ws.workspace_id,
            node_id=NODE_ID,
            container_id="c1",
            container_state="running",
            topology_id=1,
            internal_endpoint="http://10.0.0.1:8080",
            config_version=1,
            health_status=WorkspaceRuntimeHealthStatus.HEALTHY.value,
        ),
    )
    session.commit()
    session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


def test_snapshot_create_job_marks_available(
    db_session: Session,
    patch_worker_now: None,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVNEST_SNAPSHOT_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()

    owner = _seed_owner(db_session)
    wid = _seed_running_workspace(db_session, owner)
    out = snapshot_service.create_snapshot(
        db_session,
        workspace_id=wid,
        owner_user_id=owner,
        name="integration-snap",
    )
    db_session.commit()
    db_session.expire_all()

    orch = create_autospec(OrchestratorService, instance=True)
    orch.export_workspace_filesystem_snapshot.return_value = WorkspaceSnapshotOperationResult(
        workspace_id=str(wid),
        success=True,
        size_bytes=1234,
        issues=None,
    )

    run_pending_jobs(db_session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
    db_session.expire_all()

    snap = db_session.get(WorkspaceSnapshot, out.snapshot_id)
    assert snap is not None
    assert snap.status == WorkspaceSnapshotStatus.AVAILABLE.value
    assert snap.size_bytes == 1234
    job = db_session.get(WorkspaceJob, out.job_id)
    assert job is not None
    assert job.status == WorkspaceJobStatus.SUCCEEDED.value
    orch.export_workspace_filesystem_snapshot.assert_called_once()

    evs = db_session.exec(select(WorkspaceEvent).where(WorkspaceEvent.workspace_id == wid)).all()
    assert any(e.event_type == WorkspaceStreamEventType.SNAPSHOT_CREATED for e in evs)


def test_snapshot_restore_job_succeeds(
    db_session: Session,
    patch_worker_now: None,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVNEST_SNAPSHOT_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()

    owner = _seed_owner(db_session)
    wid = _seed_running_workspace(db_session, owner)
    create_out = snapshot_service.create_snapshot(
        db_session,
        workspace_id=wid,
        owner_user_id=owner,
        name="to-restore",
    )
    db_session.commit()
    db_session.expire_all()

    orch = create_autospec(OrchestratorService, instance=True)
    orch.export_workspace_filesystem_snapshot.side_effect = _export_writes_minimal_archive
    run_pending_jobs(db_session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
    db_session.expire_all()

    ws = db_session.get(Workspace, wid)
    assert ws is not None
    ws.status = WorkspaceStatus.STOPPED.value
    db_session.add(ws)
    db_session.commit()

    restore_out = snapshot_service.restore_snapshot(
        db_session,
        snapshot_id=create_out.snapshot_id,
        owner_user_id=owner,
    )
    db_session.commit()
    db_session.expire_all()

    orch.import_workspace_filesystem_snapshot.return_value = WorkspaceSnapshotOperationResult(
        workspace_id=str(wid),
        success=True,
        size_bytes=10,
        issues=None,
    )
    run_pending_jobs(db_session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
    db_session.expire_all()

    snap = db_session.get(WorkspaceSnapshot, create_out.snapshot_id)
    assert snap is not None
    assert snap.status == WorkspaceSnapshotStatus.AVAILABLE.value
    rjob = db_session.get(WorkspaceJob, restore_out.job_id)
    assert rjob is not None
    assert rjob.job_type == WorkspaceJobType.SNAPSHOT_RESTORE.value
    assert rjob.status == WorkspaceJobStatus.SUCCEEDED.value
    orch.import_workspace_filesystem_snapshot.assert_called_once()
