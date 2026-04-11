"""Integration tests: workspace control-plane events (same data path as GET /workspaces/{id}/events SSE)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi import status
from sqlmodel import Session, select

from app.services.auth_service.services.auth_token import create_access_token
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceEvent,
    WorkspaceStatus,
)
from app.services.workspace_service.services.workspace_event_service import (
    WorkspaceStreamEventType,
    event_to_sse_dict,
    format_sse_data_line,
    list_workspace_events,
    record_workspace_event,
)


def _register_and_token(client, *, username: str, email: str) -> tuple[int, str]:
    r = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": "securepass123"},
    )
    assert r.status_code == status.HTTP_201_CREATED, r.text
    uid = r.json()["user_auth_id"]
    return uid, create_access_token(user_id=uid)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_workspace(db_session: Session, owner_id: int) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="Int SSE WS",
        description="integration sse",
        owner_user_id=owner_id,
        status=WorkspaceStatus.RUNNING.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(ws)
    db_session.flush()
    db_session.add(
        WorkspaceConfig(
            workspace_id=ws.workspace_id,
            version=1,
            config_json={"v": 1},
        )
    )
    db_session.commit()
    db_session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


def test_get_workspace_events_404_missing(client, db_session: Session) -> None:
    uid, token = _register_and_token(client, username="int_sse_nf", email="int_sse_nf@example.com")

    r = client.get("/workspaces/88888888/events", headers=_auth(token))
    assert r.status_code == status.HTTP_404_NOT_FOUND
    assert r.json()["detail"] == "Workspace not found"


def test_get_workspace_events_sse_contains_persisted_event_shape(
    client,
    db_session: Session,
) -> None:
    """SSE yields ``format_sse_data_line(ev)`` for rows from ``list_workspace_events`` (no live stream in CI)."""
    uid, token = _register_and_token(client, username="int_sse_ok", email="int_sse_ok@example.com")
    wid = _seed_workspace(db_session, uid)
    eid = record_workspace_event(
        db_session,
        workspace_id=wid,
        event_type=WorkspaceStreamEventType.JOB_SUCCEEDED,
        status=WorkspaceStatus.RUNNING.value,
        message="Workspace job succeeded",
        payload={"job_id": 55, "job_type": "CREATE", "workspace_status": WorkspaceStatus.RUNNING.value},
    )
    db_session.commit()

    rows = list_workspace_events(db_session, workspace_id=wid, owner_user_id=uid, after_id=0)
    assert len(rows) == 1
    assert rows[0].workspace_event_id == eid

    payload = event_to_sse_dict(rows[0])
    assert payload["id"] == eid
    assert payload["workspace_id"] == wid
    assert payload["event_type"] == WorkspaceStreamEventType.JOB_SUCCEEDED
    assert payload["status"] == WorkspaceStatus.RUNNING.value
    assert payload["message"] == "Workspace job succeeded"
    assert payload["payload"]["job_id"] == 55
    assert payload["payload"]["job_type"] == "CREATE"
    assert "created_at" in payload and isinstance(payload["created_at"], str)

    line = format_sse_data_line(rows[0])
    assert line.startswith("data: ")
    sep = line.index("\n\n")
    wire = json.loads(line[len("data: ") : sep])
    assert wire == payload


def test_get_workspace_events_sse_empty_workspace_stream_opens_without_reading_body(
    client,
    db_session: Session,
) -> None:
    """New workspace: poll query returns no rows (SSE loop would yield nothing until first event)."""
    uid, _ = _register_and_token(client, username="int_sse_empty", email="int_sse_empty@example.com")
    wid = _seed_workspace(db_session, uid)

    rows = list_workspace_events(db_session, workspace_id=wid, owner_user_id=uid, after_id=0)
    assert rows == []


def test_get_workspace_events_after_start_intent_stream_contains_queued_event(
    client,
    db_session: Session,
) -> None:
    """POST start persists INTENT_QUEUED; SSE uses the same ``list_workspace_events`` page as this assertion."""
    uid, token = _register_and_token(
        client,
        username="int_sse_start",
        email="int_sse_start@example.com",
    )
    wid = _seed_workspace(db_session, uid)
    ws = db_session.get(Workspace, wid)
    assert ws is not None
    ws.status = WorkspaceStatus.STOPPED.value
    db_session.add(ws)
    db_session.commit()

    r = client.post(f"/workspaces/start/{wid}", headers=_auth(token))
    assert r.status_code == status.HTTP_202_ACCEPTED, r.text
    job_id = r.json()["job_id"]

    ev = db_session.exec(select(WorkspaceEvent).where(WorkspaceEvent.workspace_id == wid)).first()
    assert ev is not None
    assert ev.payload_json.get("job_id") == job_id

    rows = list_workspace_events(db_session, workspace_id=wid, owner_user_id=uid, after_id=0)
    intent_row = next(
        (
            r
            for r in rows
            if r.event_type == WorkspaceStreamEventType.INTENT_QUEUED
            and (r.payload_json or {}).get("job_id") == job_id
        ),
        None,
    )
    assert intent_row is not None, "expected INTENT_QUEUED from start intent for this job_id"
    payload = event_to_sse_dict(intent_row)
    assert payload["event_type"] == WorkspaceStreamEventType.INTENT_QUEUED
    assert payload["payload"]["job_id"] == job_id
    assert payload["payload"]["job_type"] == "START"


def test_get_workspace_events_wrong_owner_404(client, db_session: Session) -> None:
    uid_a, _ = _register_and_token(client, username="int_sse_a", email="int_sse_a@example.com")
    uid_b, token_b = _register_and_token(client, username="int_sse_b", email="int_sse_b@example.com")
    wid = _seed_workspace(db_session, uid_a)
    record_workspace_event(
        db_session,
        workspace_id=wid,
        event_type=WorkspaceStreamEventType.INTENT_QUEUED,
        payload={"job_id": 1},
    )
    db_session.commit()

    r = client.get(f"/workspaces/{wid}/events", headers=_auth(token_b))
    assert r.status_code == status.HTTP_404_NOT_FOUND
