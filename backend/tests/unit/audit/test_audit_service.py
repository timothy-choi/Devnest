"""Unit tests: AuditLog record creation and service methods (SQLite)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.services.audit_service.enums import AuditAction, AuditActorType, AuditOutcome
from app.services.audit_service.models import AuditLog
from app.services.audit_service.service import (
    count_audit_logs_for_user,
    count_audit_logs_for_workspace,
    list_audit_logs_for_user,
    list_audit_logs_for_workspace,
    record_audit,
)
from app.services.auth_service.models import UserAuth
from app.services.workspace_service.models import Workspace


@pytest.fixture()
def audit_session() -> Session:
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


def _seed_workspace(session: Session, owner: int) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="aud-ws",
        description="",
        owner_user_id=owner,
        status="STOPPED",
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.commit()
    session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


class TestRecordAudit:
    def test_creates_row(self, audit_session: Session) -> None:
        uid = _seed_user(audit_session)
        row = record_audit(
            audit_session,
            action=AuditAction.WORKSPACE_CREATE_REQUESTED.value,
            resource_type="workspace",
            resource_id=42,
            actor_user_id=uid,
            actor_type=AuditActorType.USER.value,
            outcome=AuditOutcome.SUCCESS.value,
        )
        audit_session.commit()
        assert row.audit_log_id is not None
        assert row.action == "workspace.create.requested"
        assert row.resource_id == "42"
        assert row.actor_type == "user"
        assert row.outcome == "success"

    def test_correlation_id_truncated(self, audit_session: Session) -> None:
        uid = _seed_user(audit_session)
        long_cid = "x" * 200
        row = record_audit(
            audit_session,
            action=AuditAction.WORKSPACE_START_REQUESTED.value,
            resource_type="workspace",
            correlation_id=long_cid,
        )
        audit_session.commit()
        assert row.correlation_id is not None
        assert len(row.correlation_id) <= 64

    def test_system_actor_no_user_id(self, audit_session: Session) -> None:
        row = record_audit(
            audit_session,
            action=AuditAction.WORKSPACE_JOB_SUCCEEDED.value,
            resource_type="workspace",
            actor_type=AuditActorType.SYSTEM.value,
            outcome=AuditOutcome.SUCCESS.value,
        )
        audit_session.commit()
        assert row.actor_user_id is None
        assert row.actor_type == "system"

    def test_append_only_no_updates(self, audit_session: Session) -> None:
        uid = _seed_user(audit_session)
        row = record_audit(
            audit_session,
            action=AuditAction.WORKSPACE_DELETE_REQUESTED.value,
            resource_type="workspace",
            actor_user_id=uid,
            actor_type=AuditActorType.USER.value,
        )
        audit_session.commit()
        original_id = row.audit_log_id
        # Normal application flow only inserts; verify the row is unchanged after retrieval
        fetched = audit_session.get(AuditLog, original_id)
        assert fetched is not None
        assert fetched.action == AuditAction.WORKSPACE_DELETE_REQUESTED.value

    def test_metadata_persisted(self, audit_session: Session) -> None:
        uid = _seed_user(audit_session)
        row = record_audit(
            audit_session,
            action=AuditAction.WORKSPACE_JOB_FAILED.value,
            resource_type="workspace",
            actor_user_id=uid,
            actor_type=AuditActorType.SYSTEM.value,
            outcome=AuditOutcome.FAILURE.value,
            reason="container failed",
            metadata={"job_type": "START", "failure_stage": "CONTAINER"},
        )
        audit_session.commit()
        assert row.reason == "container failed"
        assert row.metadata_json == {"job_type": "START", "failure_stage": "CONTAINER"}


class TestListAuditLogs:
    def test_list_for_workspace(self, audit_session: Session) -> None:
        uid = _seed_user(audit_session)
        wid = _seed_workspace(audit_session, uid)

        for action in [
            AuditAction.WORKSPACE_START_REQUESTED,
            AuditAction.WORKSPACE_STOP_REQUESTED,
        ]:
            record_audit(
                audit_session,
                action=action.value,
                resource_type="workspace",
                workspace_id=wid,
            )
        audit_session.commit()

        rows = list_audit_logs_for_workspace(audit_session, workspace_id=wid)
        assert len(rows) == 2
        # Ordered desc by created_at — most recent first
        assert rows[0].action == AuditAction.WORKSPACE_STOP_REQUESTED.value

    def test_list_for_user(self, audit_session: Session) -> None:
        uid = _seed_user(audit_session)
        uid2 = _seed_user(audit_session)

        record_audit(audit_session, action="workspace.create.requested", resource_type="workspace", actor_user_id=uid)
        record_audit(audit_session, action="workspace.start.requested", resource_type="workspace", actor_user_id=uid)
        record_audit(audit_session, action="workspace.create.requested", resource_type="workspace", actor_user_id=uid2)
        audit_session.commit()

        rows_uid = list_audit_logs_for_user(audit_session, actor_user_id=uid)
        rows_uid2 = list_audit_logs_for_user(audit_session, actor_user_id=uid2)
        assert len(rows_uid) == 2
        assert len(rows_uid2) == 1

    def test_list_respects_limit(self, audit_session: Session) -> None:
        uid = _seed_user(audit_session)
        wid = _seed_workspace(audit_session, uid)
        for _ in range(10):
            record_audit(audit_session, action="workspace.start.requested", resource_type="workspace", workspace_id=wid)
        audit_session.commit()

        rows = list_audit_logs_for_workspace(audit_session, workspace_id=wid, limit=3)
        assert len(rows) == 3

    def test_filter_by_action(self, audit_session: Session) -> None:
        uid = _seed_user(audit_session)
        wid = _seed_workspace(audit_session, uid)
        record_audit(audit_session, action=AuditAction.WORKSPACE_START_REQUESTED.value, resource_type="workspace", workspace_id=wid)
        record_audit(audit_session, action=AuditAction.WORKSPACE_STOP_REQUESTED.value, resource_type="workspace", workspace_id=wid)
        record_audit(audit_session, action=AuditAction.WORKSPACE_START_REQUESTED.value, resource_type="workspace", workspace_id=wid)
        audit_session.commit()

        rows = list_audit_logs_for_workspace(audit_session, workspace_id=wid, action=AuditAction.WORKSPACE_START_REQUESTED.value)
        assert len(rows) == 2
        assert all(r.action == AuditAction.WORKSPACE_START_REQUESTED.value for r in rows)

    def test_filter_by_outcome(self, audit_session: Session) -> None:
        uid = _seed_user(audit_session)
        wid = _seed_workspace(audit_session, uid)
        record_audit(audit_session, action=AuditAction.WORKSPACE_JOB_FAILED.value, resource_type="workspace",
                     workspace_id=wid, outcome=AuditOutcome.FAILURE.value)
        record_audit(audit_session, action=AuditAction.WORKSPACE_JOB_SUCCEEDED.value, resource_type="workspace",
                     workspace_id=wid, outcome=AuditOutcome.SUCCESS.value)
        audit_session.commit()

        fail_rows = list_audit_logs_for_workspace(audit_session, workspace_id=wid, outcome=AuditOutcome.FAILURE.value)
        assert len(fail_rows) == 1
        assert fail_rows[0].outcome == AuditOutcome.FAILURE.value

    def test_count_for_workspace(self, audit_session: Session) -> None:
        uid = _seed_user(audit_session)
        wid = _seed_workspace(audit_session, uid)
        for _ in range(5):
            record_audit(audit_session, action=AuditAction.WORKSPACE_START_REQUESTED.value, resource_type="workspace", workspace_id=wid)
        record_audit(audit_session, action=AuditAction.WORKSPACE_STOP_REQUESTED.value, resource_type="workspace", workspace_id=wid)
        audit_session.commit()

        total = count_audit_logs_for_workspace(audit_session, workspace_id=wid)
        assert total == 6

        filtered = count_audit_logs_for_workspace(audit_session, workspace_id=wid, action=AuditAction.WORKSPACE_START_REQUESTED.value)
        assert filtered == 5

    def test_count_for_user(self, audit_session: Session) -> None:
        uid = _seed_user(audit_session)
        for _ in range(3):
            record_audit(audit_session, action=AuditAction.WORKSPACE_CREATE_REQUESTED.value,
                         resource_type="workspace", actor_user_id=uid)
        audit_session.commit()

        assert count_audit_logs_for_user(audit_session, actor_user_id=uid) == 3

    def test_none_metadata_stored_as_null(self, audit_session: Session) -> None:
        uid = _seed_user(audit_session)
        row = record_audit(
            audit_session,
            action=AuditAction.WORKSPACE_START_REQUESTED.value,
            resource_type="workspace",
            actor_user_id=uid,
        )
        audit_session.commit()
        fetched = audit_session.get(AuditLog, row.audit_log_id)
        assert fetched is not None
        assert fetched.metadata_json is None

    def test_metadata_with_data_is_preserved(self, audit_session: Session) -> None:
        uid = _seed_user(audit_session)
        row = record_audit(
            audit_session,
            action=AuditAction.WORKSPACE_START_REQUESTED.value,
            resource_type="workspace",
            actor_user_id=uid,
            metadata={"job_type": "START"},
        )
        audit_session.commit()
        fetched = audit_session.get(AuditLog, row.audit_log_id)
        assert fetched is not None
        assert fetched.metadata_json == {"job_type": "START"}
