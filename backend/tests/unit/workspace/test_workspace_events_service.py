"""Unit tests: workspace_event_service (SQLite, no HTTP)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlmodel import Session

from app.services.auth_service.models import UserAuth
from app.services.workspace_service.errors import WorkspaceNotFoundError
from app.services.workspace_service.models import Workspace, WorkspaceEvent
from app.services.workspace_service.services.workspace_event_service import (
    WorkspaceStreamEventType,
    assert_workspace_owner,
    event_to_sse_dict,
    format_sse_data_line,
    list_workspace_events,
    record_workspace_event,
)


def _seed_owner(session: Session) -> int:
    user = UserAuth(
        username="ws_events_owner",
        email="ws_events_owner@example.com",
        password_hash="x",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.user_auth_id is not None
    return user.user_auth_id


def _seed_workspace(session: Session, owner_id: int, *, name: str = "Events WS") -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name=name,
        description="unit events",
        owner_user_id=owner_id,
        status="CREATING",
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.commit()
    session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


def test_record_workspace_event_persists_normalized_row(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id)
        eid = record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.INTENT_QUEUED,
            status="CREATING",
            message="Workspace create job queued",
            payload={"job_id": 42, "job_type": "CREATE", "requested_config_version": 1},
        )
        session.commit()

    assert isinstance(eid, int)

    with Session(workspace_unit_engine) as session:
        row = session.get(WorkspaceEvent, eid)
        assert row is not None
        assert row.workspace_id == wid
        assert row.event_type == WorkspaceStreamEventType.INTENT_QUEUED
        assert row.status == "CREATING"
        assert row.message == "Workspace create job queued"
        assert row.payload_json == {"job_id": 42, "job_type": "CREATE", "requested_config_version": 1}
        assert row.created_at is not None


def test_record_workspace_event_defaults_payload_and_optional_fields(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id)
        eid = record_workspace_event(
            session,
            workspace_id=wid,
            event_type="custom.probe",
        )
        session.commit()

    with Session(workspace_unit_engine) as session:
        row = session.get(WorkspaceEvent, eid)
        assert row is not None
        assert row.payload_json == {}
        assert row.status is None
        assert row.message is None


def test_list_workspace_events_ordered_by_id_ascending(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id)
        first = record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.JOB_RUNNING,
            payload={"job_id": 1},
        )
        second = record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.JOB_SUCCEEDED,
            status="RUNNING",
            payload={"job_id": 1, "workspace_status": "RUNNING"},
        )
        session.commit()
        assert first < second

    with Session(workspace_unit_engine) as session:
        rows = list_workspace_events(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            after_id=0,
            limit=50,
        )

    assert len(rows) == 2
    assert rows[0].workspace_event_id == first
    assert rows[0].event_type == WorkspaceStreamEventType.JOB_RUNNING
    assert rows[1].workspace_event_id == second
    assert rows[1].event_type == WorkspaceStreamEventType.JOB_SUCCEEDED


def test_list_workspace_events_after_id_cursor(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id)
        e1 = record_workspace_event(session, workspace_id=wid, event_type="a", payload={})
        e2 = record_workspace_event(session, workspace_id=wid, event_type="b", payload={})
        session.commit()

    with Session(workspace_unit_engine) as session:
        page = list_workspace_events(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            after_id=e1,
            limit=50,
        )
    assert len(page) == 1
    assert page[0].workspace_event_id == e2


def test_list_workspace_events_empty(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id)

    with Session(workspace_unit_engine) as session:
        rows = list_workspace_events(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
        )
    assert rows == []


def test_list_workspace_events_limit_clamped(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id)
        for i in range(5):
            record_workspace_event(session, workspace_id=wid, event_type=f"t{i}", payload={"i": i})
        session.commit()

    with Session(workspace_unit_engine) as session:
        low = list_workspace_events(session, workspace_id=wid, owner_user_id=owner_user_id, limit=0)
        assert len(low) == 1
        high = list_workspace_events(session, workspace_id=wid, owner_user_id=owner_user_id, limit=99999)
        assert len(high) == 5


def test_list_workspace_events_workspace_not_found(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        with pytest.raises(WorkspaceNotFoundError, match="Workspace not found"):
            list_workspace_events(
                session,
                workspace_id=999_999,
                owner_user_id=owner_user_id,
            )


def test_list_workspace_events_wrong_owner(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id)
        record_workspace_event(session, workspace_id=wid, event_type="x", payload={})
        other = UserAuth(username="other_e", email="other_e@example.com", password_hash="h")
        session.add(other)
        session.commit()
        session.refresh(other)
        other_id = other.user_auth_id
        assert other_id is not None

    with Session(workspace_unit_engine) as session:
        with pytest.raises(WorkspaceNotFoundError, match="Workspace not found"):
            list_workspace_events(
                session,
                workspace_id=wid,
                owner_user_id=other_id,
            )


def test_assert_workspace_owner_success_and_failure(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id)
        assert_workspace_owner(session, wid, owner_user_id)

    with Session(workspace_unit_engine) as session:
        with pytest.raises(WorkspaceNotFoundError):
            assert_workspace_owner(session, 999_999, owner_user_id)


def test_event_to_sse_dict_normalized_shape(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id)
        eid = record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.JOB_FAILED,
            status="ERROR",
            message="orchestrator error",
            payload={"job_id": 9, "error_msg": "boom"},
        )
        session.commit()
        row = session.get(WorkspaceEvent, eid)
        assert row is not None

    d = event_to_sse_dict(row)
    assert d["id"] == eid
    assert d["workspace_id"] == wid
    assert d["event_type"] == WorkspaceStreamEventType.JOB_FAILED
    assert d["status"] == "ERROR"
    assert d["message"] == "orchestrator error"
    assert d["payload"] == {"job_id": 9, "error_msg": "boom"}
    assert isinstance(d["created_at"], str)
    assert "T" in d["created_at"]


def test_format_sse_data_line_is_valid_sse_json(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id)
        eid = record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.INTENT_QUEUED,
            status="STARTING",
            message="Intent accepted",
            payload={"job_type": "START"},
        )
        session.commit()
        row = session.get(WorkspaceEvent, eid)
        assert row is not None

    line = format_sse_data_line(row)
    assert line.startswith("data: ")
    assert line.endswith("\n\n")
    payload_json = line[len("data: ") :].strip()
    parsed = json.loads(payload_json)
    assert parsed["event_type"] == WorkspaceStreamEventType.INTENT_QUEUED
    assert parsed["payload"]["job_type"] == "START"
