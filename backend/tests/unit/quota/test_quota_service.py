"""Unit tests for quota enforcement logic (SQLite in-memory)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.services.auth_service.models import UserAuth
from app.services.policy_service.enums import ScopeType
from app.services.quota_service.errors import QuotaExceededError
from app.services.quota_service.models import Quota
from app.services.quota_service.service import (
    check_running_workspace_quota,
    check_session_quota,
    check_snapshot_quota,
    check_workspace_quota,
)
from app.services.workspace_service.models import Workspace, WorkspaceSnapshot
from app.services.workspace_service.models.enums import (
    WorkspaceSnapshotStatus,
    WorkspaceStatus,
)


@pytest.fixture()
def quota_session() -> Session:
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
    return u.user_auth_id  # type: ignore[return-value]


def _seed_workspace(session: Session, owner: int, status: str = WorkspaceStatus.STOPPED.value) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name=f"ws_{uuid.uuid4().hex[:6]}",
        description="",
        owner_user_id=owner,
        status=status,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws.workspace_id  # type: ignore[return-value]


def _seed_snapshot(session: Session, workspace_id: int, owner_user_id: int, status: str = WorkspaceSnapshotStatus.AVAILABLE.value) -> None:
    now = datetime.now(timezone.utc)
    snap = WorkspaceSnapshot(
        workspace_id=workspace_id,
        name=f"snap_{uuid.uuid4().hex[:6]}",
        storage_uri=f"file:///tmp/snap_{uuid.uuid4().hex[:8]}.tar.gz",
        status=status,
        created_by_user_id=owner_user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(snap)
    session.commit()


def _add_quota(session: Session, *, scope_type: ScopeType, scope_id: int | None = None, **limits) -> Quota:
    now = datetime.now(timezone.utc)
    q = Quota(
        scope_type=scope_type.value,
        scope_id=scope_id,
        created_at=now,
        updated_at=now,
        **limits,
    )
    session.add(q)
    session.commit()
    return q


# ---------------------------------------------------------------------------
# check_workspace_quota
# ---------------------------------------------------------------------------

class TestCheckWorkspaceQuota:
    def test_no_quota_always_passes(self, quota_session: Session) -> None:
        uid = _seed_user(quota_session)
        check_workspace_quota(quota_session, owner_user_id=uid)

    def test_under_limit_passes(self, quota_session: Session) -> None:
        uid = _seed_user(quota_session)
        _add_quota(quota_session, scope_type=ScopeType.USER, scope_id=uid, max_workspaces=3)
        _seed_workspace(quota_session, uid)
        _seed_workspace(quota_session, uid)
        check_workspace_quota(quota_session, owner_user_id=uid)

    def test_at_limit_raises(self, quota_session: Session) -> None:
        uid = _seed_user(quota_session)
        _add_quota(quota_session, scope_type=ScopeType.USER, scope_id=uid, max_workspaces=2)
        _seed_workspace(quota_session, uid)
        _seed_workspace(quota_session, uid)
        with pytest.raises(QuotaExceededError) as exc_info:
            check_workspace_quota(quota_session, owner_user_id=uid)
        assert exc_info.value.quota_field == "max_workspaces"
        assert exc_info.value.limit == 2
        assert exc_info.value.current == 2

    def test_deleted_workspaces_not_counted(self, quota_session: Session) -> None:
        uid = _seed_user(quota_session)
        _add_quota(quota_session, scope_type=ScopeType.USER, scope_id=uid, max_workspaces=1)
        _seed_workspace(quota_session, uid, status=WorkspaceStatus.DELETED.value)
        check_workspace_quota(quota_session, owner_user_id=uid)

    def test_global_quota_applies_when_no_user_quota(self, quota_session: Session) -> None:
        uid = _seed_user(quota_session)
        _add_quota(quota_session, scope_type=ScopeType.GLOBAL, scope_id=None, max_workspaces=1)
        _seed_workspace(quota_session, uid)
        with pytest.raises(QuotaExceededError):
            check_workspace_quota(quota_session, owner_user_id=uid)

    def test_user_quota_takes_precedence_over_global(self, quota_session: Session) -> None:
        uid = _seed_user(quota_session)
        _add_quota(quota_session, scope_type=ScopeType.GLOBAL, scope_id=None, max_workspaces=1)
        _add_quota(quota_session, scope_type=ScopeType.USER, scope_id=uid, max_workspaces=5)
        for _ in range(3):
            _seed_workspace(quota_session, uid)
        check_workspace_quota(quota_session, owner_user_id=uid)

    def test_exceeded_records_audit_row(self, quota_session: Session) -> None:
        from app.services.audit_service.models import AuditLog
        from sqlmodel import select as sel

        uid = _seed_user(quota_session)
        _add_quota(quota_session, scope_type=ScopeType.USER, scope_id=uid, max_workspaces=0)
        with pytest.raises(QuotaExceededError):
            check_workspace_quota(quota_session, owner_user_id=uid)

        rows = quota_session.exec(
            sel(AuditLog).where(AuditLog.action == "quota.exceeded")
        ).all()
        assert len(rows) == 1
        assert rows[0].outcome == "denied"


# ---------------------------------------------------------------------------
# check_running_workspace_quota
# ---------------------------------------------------------------------------

class TestCheckRunningWorkspaceQuota:
    def test_no_quota_passes(self, quota_session: Session) -> None:
        uid = _seed_user(quota_session)
        check_running_workspace_quota(quota_session, owner_user_id=uid)

    def test_under_limit_passes(self, quota_session: Session) -> None:
        uid = _seed_user(quota_session)
        _add_quota(quota_session, scope_type=ScopeType.USER, scope_id=uid, max_running_workspaces=2)
        _seed_workspace(quota_session, uid, status=WorkspaceStatus.RUNNING.value)
        check_running_workspace_quota(quota_session, owner_user_id=uid)

    def test_at_limit_raises(self, quota_session: Session) -> None:
        uid = _seed_user(quota_session)
        _add_quota(quota_session, scope_type=ScopeType.USER, scope_id=uid, max_running_workspaces=1)
        _seed_workspace(quota_session, uid, status=WorkspaceStatus.RUNNING.value)
        with pytest.raises(QuotaExceededError) as exc_info:
            check_running_workspace_quota(quota_session, owner_user_id=uid)
        assert exc_info.value.quota_field == "max_running_workspaces"

    def test_workspace_id_excluded_from_count(self, quota_session: Session) -> None:
        """The workspace being started is excluded so re-checking doesn't double count."""
        uid = _seed_user(quota_session)
        _add_quota(quota_session, scope_type=ScopeType.USER, scope_id=uid, max_running_workspaces=1)
        wid = _seed_workspace(quota_session, uid, status=WorkspaceStatus.RUNNING.value)
        check_running_workspace_quota(quota_session, owner_user_id=uid, workspace_id=wid)

    def test_starting_status_counts_as_running(self, quota_session: Session) -> None:
        uid = _seed_user(quota_session)
        _add_quota(quota_session, scope_type=ScopeType.USER, scope_id=uid, max_running_workspaces=1)
        _seed_workspace(quota_session, uid, status=WorkspaceStatus.STARTING.value)
        with pytest.raises(QuotaExceededError):
            check_running_workspace_quota(quota_session, owner_user_id=uid)


# ---------------------------------------------------------------------------
# check_snapshot_quota
# ---------------------------------------------------------------------------

class TestCheckSnapshotQuota:
    def test_no_quota_passes(self, quota_session: Session) -> None:
        uid = _seed_user(quota_session)
        wid = _seed_workspace(quota_session, uid)
        check_snapshot_quota(quota_session, workspace_id=wid, owner_user_id=uid)

    def test_under_limit_passes(self, quota_session: Session) -> None:
        uid = _seed_user(quota_session)
        wid = _seed_workspace(quota_session, uid)
        _add_quota(quota_session, scope_type=ScopeType.WORKSPACE, scope_id=wid, max_snapshots=3)
        _seed_snapshot(quota_session, wid, uid)
        check_snapshot_quota(quota_session, workspace_id=wid, owner_user_id=uid)

    def test_at_limit_raises(self, quota_session: Session) -> None:
        uid = _seed_user(quota_session)
        wid = _seed_workspace(quota_session, uid)
        _add_quota(quota_session, scope_type=ScopeType.WORKSPACE, scope_id=wid, max_snapshots=1)
        _seed_snapshot(quota_session, wid, uid)
        with pytest.raises(QuotaExceededError) as exc_info:
            check_snapshot_quota(quota_session, workspace_id=wid, owner_user_id=uid)
        assert exc_info.value.quota_field == "max_snapshots"

    def test_failed_snapshots_not_counted(self, quota_session: Session) -> None:
        uid = _seed_user(quota_session)
        wid = _seed_workspace(quota_session, uid)
        _add_quota(quota_session, scope_type=ScopeType.WORKSPACE, scope_id=wid, max_snapshots=1)
        _seed_snapshot(quota_session, wid, uid, status=WorkspaceSnapshotStatus.FAILED.value)
        check_snapshot_quota(quota_session, workspace_id=wid, owner_user_id=uid)


# ---------------------------------------------------------------------------
# check_session_quota
# ---------------------------------------------------------------------------

class TestCheckSessionQuota:
    def test_no_quota_passes(self, quota_session: Session) -> None:
        uid = _seed_user(quota_session)
        wid = _seed_workspace(quota_session, uid)
        check_session_quota(quota_session, workspace_id=wid, owner_user_id=uid, current_session_count=5)

    def test_under_limit_passes(self, quota_session: Session) -> None:
        uid = _seed_user(quota_session)
        wid = _seed_workspace(quota_session, uid)
        _add_quota(quota_session, scope_type=ScopeType.WORKSPACE, scope_id=wid, max_sessions=3)
        check_session_quota(quota_session, workspace_id=wid, owner_user_id=uid, current_session_count=2)

    def test_at_limit_raises(self, quota_session: Session) -> None:
        uid = _seed_user(quota_session)
        wid = _seed_workspace(quota_session, uid)
        _add_quota(quota_session, scope_type=ScopeType.WORKSPACE, scope_id=wid, max_sessions=2)
        with pytest.raises(QuotaExceededError) as exc_info:
            check_session_quota(quota_session, workspace_id=wid, owner_user_id=uid, current_session_count=2)
        assert exc_info.value.quota_field == "max_sessions"
        assert exc_info.value.limit == 2
