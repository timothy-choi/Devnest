"""Integration tests: workspace_event_service on PostgreSQL (worker DB, truncate per test)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func
from sqlmodel import Session, select

from app.services.auth_service.models import UserAuth
from app.services.workspace_service.errors import WorkspaceNotFoundError
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceEvent,
    WorkspaceStatus,
)
from app.services.workspace_service.services import workspace_intent_service
from app.services.workspace_service.services.workspace_event_service import (
    WorkspaceStreamEventType,
    assert_workspace_owner,
    list_workspace_events,
    record_workspace_event,
)


def _seed_owner(session: Session) -> int:
    user = UserAuth(
        username="int_ws_events_owner",
        email="int_ws_events_owner@example.com",
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.user_auth_id is not None
    return user.user_auth_id


def _seed_workspace(session: Session, owner_id: int, *, name: str = "Int Events WS") -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name=name,
        description="integration events",
        owner_user_id=owner_id,
        status=WorkspaceStatus.RUNNING.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.flush()
    session.add(
        WorkspaceConfig(
            workspace_id=ws.workspace_id,
            version=1,
            config_json={"k": 1},
        )
    )
    session.commit()
    session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


def test_record_workspace_event_persisted_fields_postgres(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace(db_session, owner_id)

    eid = record_workspace_event(
        db_session,
        workspace_id=wid,
        event_type=WorkspaceStreamEventType.INTENT_QUEUED,
        status=WorkspaceStatus.CREATING.value,
        message="Workspace create job queued",
        payload={"job_id": 101, "job_type": "CREATE", "requested_config_version": 1},
    )
    db_session.commit()

    row = db_session.get(WorkspaceEvent, eid)
    assert row is not None
    assert row.workspace_id == wid
    assert row.event_type == WorkspaceStreamEventType.INTENT_QUEUED
    assert row.status == WorkspaceStatus.CREATING.value
    assert row.message == "Workspace create job queued"
    assert row.payload_json == {"job_id": 101, "job_type": "CREATE", "requested_config_version": 1}
    assert row.created_at is not None


def test_list_workspace_events_ordering_and_workspace_scope(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid_a = _seed_workspace(db_session, owner_id, name="WS A")
    wid_b = _seed_workspace(db_session, owner_id, name="WS B")

    e1 = record_workspace_event(
        db_session,
        workspace_id=wid_a,
        event_type=WorkspaceStreamEventType.JOB_RUNNING,
        payload={"job_id": 1},
    )
    record_workspace_event(
        db_session,
        workspace_id=wid_b,
        event_type="noise.other_ws",
        payload={},
    )
    e2 = record_workspace_event(
        db_session,
        workspace_id=wid_a,
        event_type=WorkspaceStreamEventType.JOB_SUCCEEDED,
        status=WorkspaceStatus.RUNNING.value,
        payload={"job_id": 1, "workspace_status": WorkspaceStatus.RUNNING.value},
    )
    db_session.commit()
    assert e1 < e2

    rows = list_workspace_events(
        db_session,
        workspace_id=wid_a,
        owner_user_id=owner_id,
        after_id=0,
        limit=50,
    )
    assert len(rows) == 2
    assert [r.workspace_event_id for r in rows] == [e1, e2]
    assert rows[0].event_type == WorkspaceStreamEventType.JOB_RUNNING
    assert rows[1].event_type == WorkspaceStreamEventType.JOB_SUCCEEDED


def test_list_workspace_events_empty(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace(db_session, owner_id)

    rows = list_workspace_events(
        db_session,
        workspace_id=wid,
        owner_user_id=owner_id,
    )
    assert rows == []


def test_list_workspace_events_not_found_wrong_owner(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace(db_session, owner_id)
    other = UserAuth(
        username="int_events_other",
        email="int_events_other@example.com",
        password_hash="h",
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    other_id = other.user_auth_id
    assert other_id is not None

    with pytest.raises(WorkspaceNotFoundError, match="Workspace not found"):
        list_workspace_events(db_session, workspace_id=wid, owner_user_id=other_id)

    with pytest.raises(WorkspaceNotFoundError, match="Workspace not found"):
        list_workspace_events(db_session, workspace_id=999_999_999, owner_user_id=owner_id)


def test_assert_workspace_owner_postgres(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace(db_session, owner_id)
    assert_workspace_owner(db_session, wid, owner_id)


def test_request_start_emits_intent_queued_event_integration(db_session: Session) -> None:
    """Real intent path persists ``controlplane.intent_queued`` in the same transaction as the job."""
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace(db_session, owner_id, name="Start Intent")
    ws = db_session.get(Workspace, wid)
    assert ws is not None
    ws.status = WorkspaceStatus.STOPPED.value
    db_session.add(ws)
    db_session.commit()

    out = workspace_intent_service.request_start_workspace(
        db_session,
        workspace_id=wid,
        owner_user_id=owner_id,
        requested_by_user_id=owner_id,
    )
    assert out.job_type == "START"

    ev = db_session.exec(
        select(WorkspaceEvent).where(WorkspaceEvent.workspace_id == wid),
    ).first()
    assert ev is not None
    assert ev.event_type == WorkspaceStreamEventType.INTENT_QUEUED
    assert ev.status == WorkspaceStatus.STARTING.value
    assert ev.payload_json.get("job_id") == out.job_id
    assert ev.payload_json.get("job_type") == "START"

    n = int(
        db_session.exec(
            select(func.count()).select_from(WorkspaceEvent).where(WorkspaceEvent.workspace_id == wid),
        ).one(),
    )
    assert n == 1
