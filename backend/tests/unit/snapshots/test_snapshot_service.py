"""Unit tests: snapshot_service validation and enqueue (SQLite)."""

from __future__ import annotations

import tempfile
import uuid
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.services.auth_service.models import UserAuth
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceJob,
    WorkspaceSnapshot,
)
from app.services.workspace_service.models.enums import (
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceSnapshotStatus,
    WorkspaceStatus,
)
from app.services.workspace_service.errors import (
    SnapshotConflictError,
    WorkspaceInvalidStateError,
)
from app.services.workspace_service.services import snapshot_service


@pytest.fixture()
def snap_session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _seed_user(session: Session) -> int:
    u = UserAuth(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@t.dev",
        password_hash="x",
        created_at=datetime.now(timezone.utc),
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    assert u.user_auth_id is not None
    return u.user_auth_id


def _seed_workspace(session: Session, owner: int, *, status: str) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="snap-ws",
        description="",
        owner_user_id=owner,
        status=status,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.flush()
    session.add(
        WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}),
    )
    session.commit()
    session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


def test_create_snapshot_rejects_invalid_workspace_status(snap_session: Session) -> None:
    owner = _seed_user(snap_session)
    wid = _seed_workspace(snap_session, owner, status=WorkspaceStatus.CREATING.value)
    with pytest.raises(WorkspaceInvalidStateError):
        snapshot_service.create_snapshot(
            snap_session,
            workspace_id=wid,
            owner_user_id=owner,
            name="s1",
        )


def test_create_snapshot_enqueues_job(snap_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("DEVNEST_SNAPSHOT_STORAGE_ROOT", tmp)
        from app.libs.common.config import get_settings

        get_settings.cache_clear()

        owner = _seed_user(snap_session)
        wid = _seed_workspace(snap_session, owner, status=WorkspaceStatus.RUNNING.value)
        out = snapshot_service.create_snapshot(
            snap_session,
            workspace_id=wid,
            owner_user_id=owner,
            name="backup-a",
            description="d",
            metadata={"k": "v"},
        )
        assert out.snapshot_id > 0
        assert out.job_id > 0
        snap = snap_session.get(WorkspaceSnapshot, out.snapshot_id)
        assert snap is not None
        assert snap.status == WorkspaceSnapshotStatus.CREATING.value
        assert snap.metadata_json == {"k": "v"}
        job = snap_session.get(WorkspaceJob, out.job_id)
        assert job is not None
        assert job.job_type == WorkspaceJobType.SNAPSHOT_CREATE.value
        assert job.workspace_snapshot_id == out.snapshot_id
        assert job.status == WorkspaceJobStatus.QUEUED.value


def test_create_snapshot_conflict_when_pending_job(snap_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("DEVNEST_SNAPSHOT_STORAGE_ROOT", tmp)
        from app.libs.common.config import get_settings

        get_settings.cache_clear()

        owner = _seed_user(snap_session)
        wid = _seed_workspace(snap_session, owner, status=WorkspaceStatus.RUNNING.value)
        snapshot_service.create_snapshot(
            snap_session,
            workspace_id=wid,
            owner_user_id=owner,
            name="a",
        )
        with pytest.raises(SnapshotConflictError):
            snapshot_service.create_snapshot(
                snap_session,
                workspace_id=wid,
                owner_user_id=owner,
                name="b",
            )


def test_restore_snapshot_rejects_missing_archive(snap_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("DEVNEST_SNAPSHOT_STORAGE_ROOT", tmp)
        from app.libs.common.config import get_settings

        get_settings.cache_clear()

        owner = _seed_user(snap_session)
        wid = _seed_workspace(snap_session, owner, status=WorkspaceStatus.STOPPED.value)
        snap = WorkspaceSnapshot(
            workspace_id=wid,
            name="orphan-meta",
            storage_uri="pending",
            status=WorkspaceSnapshotStatus.AVAILABLE.value,
            created_by_user_id=owner,
        )
        snap_session.add(snap)
        snap_session.flush()
        assert snap.workspace_snapshot_id is not None
        from app.services.storage.factory import get_snapshot_storage_provider

        storage = get_snapshot_storage_provider()
        snap.storage_uri = storage.storage_uri(workspace_id=wid, snapshot_id=snap.workspace_snapshot_id)
        snap_session.add(snap)
        snap_session.commit()

        with pytest.raises(WorkspaceInvalidStateError, match="missing or empty"):
            snapshot_service.restore_snapshot(
                snap_session,
                snapshot_id=snap.workspace_snapshot_id,
                owner_user_id=owner,
            )
