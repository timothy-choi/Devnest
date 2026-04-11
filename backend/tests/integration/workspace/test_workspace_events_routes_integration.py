"""Integration tests: GET /workspaces/{id}/events (SSE) on PostgreSQL (real app + DB)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
from httpx import ASGITransport, Client, Timeout


def _asgi_transport(app):
    """httpx 0.28+ supports ``lifespan=``; 0.27.x does not (CI may resolve either)."""
    try:
        return ASGITransport(app=app, lifespan="auto")
    except TypeError:
        return ASGITransport(app=app)
from fastapi import status
from sqlmodel import Session, select

from app.services.auth_service.services.auth_token import create_access_token
from app.services.workspace_service.api.routers import workspaces as workspaces_router
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceEvent,
    WorkspaceStatus,
)
from app.services.workspace_service.services.workspace_event_service import (
    WorkspaceStreamEventType,
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


@pytest.fixture(autouse=True)
def _fast_sse_poll_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSE handler sleeps between polls; shorten globally so bounded reads cannot stall CI."""
    monkeypatch.setattr(workspaces_router, "SSE_POLL_INTERVAL_SEC", 0.01)


def _read_sse_until_data_line(
    testclient,
    path: str,
    headers: dict[str, str],
    *,
    max_bytes: int = 64_000,
    max_chunks: int = 256,
    read_timeout_s: float = 20.0,
) -> bytes:
    """
    Read the first full SSE ``data:`` frame using httpx + ASGITransport.

    Starlette's synchronous ``TestClient.stream().iter_bytes()`` can block without returning even
    when ``timeout=`` is passed (especially under ``pytest-xdist``), which stalls the whole worker
    and makes the *other* worker look stuck on its last test. httpx applies read timeouts to stream
    reads reliably.
    """
    buf = b""
    n_chunks = 0
    transport = _asgi_transport(testclient.app)
    timeout = Timeout(connect=5.0, read=read_timeout_s, write=10.0, pool=5.0)
    with Client(transport=transport, base_url="http://testserver", timeout=timeout) as http:
        try:
            with http.stream("GET", path, headers=headers) as res:
                assert res.status_code == status.HTTP_200_OK
                assert res.headers.get("content-type", "").startswith("text/event-stream")
                for chunk in res.iter_bytes(chunk_size=512):
                    n_chunks += 1
                    buf += chunk
                    if b"data: " in buf and b"\n\n" in buf:
                        return buf
                    if len(buf) >= max_bytes or n_chunks >= max_chunks:
                        break
        except httpx.ReadTimeout as e:
            pytest.fail(
                f"SSE read timed out after {read_timeout_s}s (chunks={n_chunks}, bytes={len(buf)}): {e}"
            )
    if b"data: " not in buf or b"\n\n" not in buf:
        pytest.fail(
            f"SSE incomplete after {n_chunks} chunks / {len(buf)} bytes (expected a full data:…\\n\\n frame)"
        )
    return buf


def test_get_workspace_events_404_missing(client, db_session: Session) -> None:
    uid, token = _register_and_token(client, username="int_sse_nf", email="int_sse_nf@example.com")

    r = client.get("/workspaces/88888888/events", headers=_auth(token))
    assert r.status_code == status.HTTP_404_NOT_FOUND
    assert r.json()["detail"] == "Workspace not found"


def test_get_workspace_events_sse_contains_persisted_event_shape(
    client,
    db_session: Session,
) -> None:
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

    raw = _read_sse_until_data_line(
        client,
        f"/workspaces/{wid}/events",
        _auth(token),
    )
    assert b"data: " in raw
    line = raw.split(b"\n\n", 1)[0].decode("utf-8")
    assert line.startswith("data: ")
    payload = json.loads(line[len("data: ") :])
    assert payload["id"] == eid
    assert payload["workspace_id"] == wid
    assert payload["event_type"] == WorkspaceStreamEventType.JOB_SUCCEEDED
    assert payload["status"] == WorkspaceStatus.RUNNING.value
    assert payload["message"] == "Workspace job succeeded"
    assert payload["payload"]["job_id"] == 55
    assert payload["payload"]["job_type"] == "CREATE"
    assert "created_at" in payload and isinstance(payload["created_at"], str)


def test_get_workspace_events_sse_empty_workspace_stream_opens_without_reading_body(
    client,
    db_session: Session,
) -> None:
    """No events: handler only writes after polling; reading the body would block until bytes exist."""
    uid, token = _register_and_token(client, username="int_sse_empty", email="int_sse_empty@example.com")
    wid = _seed_workspace(db_session, uid)

    transport = _asgi_transport(client.app)
    timeout = Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
    with Client(transport=transport, base_url="http://testserver", timeout=timeout) as http:
        with http.stream("GET", f"/workspaces/{wid}/events", headers=_auth(token)) as res:
            assert res.status_code == status.HTTP_200_OK
            assert res.headers.get("content-type", "").startswith("text/event-stream")
            assert res.headers.get("cache-control") == "no-cache"
    # Do not read the SSE body here (infinite stream); headers prove the route opened.


def test_get_workspace_events_after_start_intent_stream_contains_queued_event(
    client,
    db_session: Session,
) -> None:
    """Control-plane: POST start enqueues job + intent event; SSE can observe the same row."""
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

    raw = _read_sse_until_data_line(client, f"/workspaces/{wid}/events", _auth(token))
    first_line = raw.split(b"\n\n", 1)[0].decode("utf-8")
    assert first_line.startswith("data: ")
    payload = json.loads(first_line[len("data: ") :])
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
