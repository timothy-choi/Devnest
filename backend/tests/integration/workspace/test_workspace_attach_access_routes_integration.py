"""Integration tests: attach/access HTTP routes on PostgreSQL (real app + DB)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import status
from sqlmodel import Session, select

from app.services.auth_service.services.auth_token import create_access_token
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceRuntime,
    WorkspaceRuntimeHealthStatus,
    WorkspaceStatus,
)
from app.services.workspace_service.services.workspace_session_service import WORKSPACE_SESSION_HTTP_HEADER

ENDPOINT_REF = "node-1:12345"
PUBLIC_HOST = "ws-123.devnest.local"
INTERNAL_EP = "10.128.0.10:8080"
CONTAINER_ID = "ctr-route-int"


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


def _auth_and_ws_session(access_token: str, workspace_session_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        WORKSPACE_SESSION_HTTP_HEADER: workspace_session_token,
    }


def _seed_ready_workspace_with_runtime(
    db_session: Session,
    owner_id: int,
    *,
    active_sessions_count: int = 0,
) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="Route Attach Int WS",
        description="integration",
        owner_user_id=owner_id,
        status=WorkspaceStatus.RUNNING.value,
        is_private=True,
        endpoint_ref=ENDPOINT_REF,
        public_host=PUBLIC_HOST,
        active_sessions_count=active_sessions_count,
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
    db_session.add(
        WorkspaceRuntime(
            workspace_id=ws.workspace_id,
            node_id="node-route-1",
            container_id=CONTAINER_ID,
            container_state="running",
            topology_id=50,
            internal_endpoint=INTERNAL_EP,
            config_version=1,
            health_status=WorkspaceRuntimeHealthStatus.HEALTHY.value,
        )
    )
    db_session.commit()
    db_session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


def test_post_attach_200_reflects_persisted_workspace_and_runtime(client, db_session: Session) -> None:
    uid, token = _register_and_token(client, username="int_attach_ok", email="int_attach_ok@example.com")
    wid = _seed_ready_workspace_with_runtime(db_session, uid, active_sessions_count=0)

    r = client.post(f"/workspaces/attach/{wid}", headers=_auth(token))
    assert r.status_code == status.HTTP_200_OK, r.text
    data = r.json()
    assert data["accepted"] is True
    assert data["workspace_id"] == wid
    assert data["status"] == WorkspaceStatus.RUNNING.value
    assert data["runtime_ready"] is True
    assert data["endpoint_ref"] == ENDPOINT_REF
    assert data["public_host"] == PUBLIC_HOST
    assert data["internal_endpoint"] == INTERNAL_EP
    assert data["gateway_url"] is None
    assert data["issues"] == []
    assert data["active_sessions_count"] == 1
    assert data["session_token"].startswith("dnws_")

    ws = db_session.get(Workspace, wid)
    assert ws is not None
    assert ws.active_sessions_count == 1
    rt = db_session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
    assert rt is not None
    assert rt.internal_endpoint == INTERNAL_EP


def test_get_access_200_reflects_persisted_rows_and_does_not_bump_sessions(
    client,
    db_session: Session,
) -> None:
    uid, token = _register_and_token(client, username="int_access_ok", email="int_access_ok@example.com")
    wid = _seed_ready_workspace_with_runtime(db_session, uid, active_sessions_count=0)

    att = client.post(f"/workspaces/attach/{wid}", headers=_auth(token))
    assert att.status_code == status.HTTP_200_OK, att.text
    ws_tok = att.json()["session_token"]

    r = client.get(f"/workspaces/{wid}/access", headers=_auth_and_ws_session(token, ws_tok))
    assert r.status_code == status.HTTP_200_OK, r.text
    data = r.json()
    assert data["success"] is True
    assert data["workspace_id"] == wid
    assert data["status"] == WorkspaceStatus.RUNNING.value
    assert data["runtime_ready"] is True
    assert data["endpoint_ref"] == ENDPOINT_REF
    assert data["public_host"] == PUBLIC_HOST
    assert data["internal_endpoint"] == INTERNAL_EP
    assert data["gateway_url"] is None
    assert data["issues"] == []

    ws = db_session.get(Workspace, wid)
    assert ws is not None
    assert ws.active_sessions_count == 1

    r2 = client.get(f"/workspaces/{wid}/access", headers=_auth_and_ws_session(token, ws_tok))
    assert r2.status_code == status.HTTP_200_OK, r2.text
    ws2 = db_session.get(Workspace, wid)
    assert ws2 is not None
    assert ws2.active_sessions_count == 1


def test_get_access_403_without_workspace_session(client, db_session: Session) -> None:
    uid, token = _register_and_token(client, username="int_access_403", email="int_access_403@example.com")
    wid = _seed_ready_workspace_with_runtime(db_session, uid)

    r = client.get(f"/workspaces/{wid}/access", headers=_auth(token))
    assert r.status_code == status.HTTP_403_FORBIDDEN
    assert "session" in r.json()["detail"].lower()


def test_post_attach_404_missing_workspace(client, db_session: Session) -> None:
    uid, token = _register_and_token(client, username="int_attach_nf", email="int_attach_nf@example.com")

    r = client.post("/workspaces/attach/88888888", headers=_auth(token))
    assert r.status_code == status.HTTP_404_NOT_FOUND
    assert r.json()["detail"] == "Workspace not found"


def test_get_access_404_missing_workspace(client, db_session: Session) -> None:
    uid, token = _register_and_token(client, username="int_access_nf", email="int_access_nf@example.com")

    r = client.get("/workspaces/88888888/access", headers=_auth(token))
    assert r.status_code == status.HTTP_404_NOT_FOUND
    assert r.json()["detail"] == "Workspace not found"


def test_post_attach_409_when_stopped(client, db_session: Session) -> None:
    uid, token = _register_and_token(client, username="int_attach_stopped", email="int_attach_stopped@example.com")
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="StoppedOnly",
        owner_user_id=uid,
        status=WorkspaceStatus.STOPPED.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(ws)
    db_session.flush()
    db_session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}))
    db_session.commit()
    db_session.refresh(ws)
    wid = ws.workspace_id

    r = client.post(f"/workspaces/attach/{wid}", headers=_auth(token))
    assert r.status_code == status.HTTP_409_CONFLICT
    assert "RUNNING" in r.json()["detail"]


def test_get_access_409_when_stopped(client, db_session: Session) -> None:
    uid, token = _register_and_token(client, username="int_access_stopped", email="int_access_stopped@example.com")
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="StoppedOnly",
        owner_user_id=uid,
        status=WorkspaceStatus.STOPPED.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(ws)
    db_session.flush()
    db_session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}))
    db_session.commit()
    db_session.refresh(ws)
    wid = ws.workspace_id

    r = client.get(f"/workspaces/{wid}/access", headers=_auth(token))
    assert r.status_code == status.HTTP_409_CONFLICT


def test_post_attach_409_running_without_runtime_row(client, db_session: Session) -> None:
    uid, token = _register_and_token(client, username="int_attach_nort", email="int_attach_nort@example.com")
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="RunningNoRt",
        owner_user_id=uid,
        status=WorkspaceStatus.RUNNING.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(ws)
    db_session.flush()
    db_session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}))
    db_session.commit()
    db_session.refresh(ws)
    wid = ws.workspace_id

    r = client.post(f"/workspaces/attach/{wid}", headers=_auth(token))
    assert r.status_code == status.HTTP_409_CONFLICT
    assert "not ready for access" in r.json()["detail"]


def test_get_access_409_running_without_runtime_row(client, db_session: Session) -> None:
    uid, token = _register_and_token(client, username="int_access_nort", email="int_access_nort@example.com")
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="RunningNoRt",
        owner_user_id=uid,
        status=WorkspaceStatus.RUNNING.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(ws)
    db_session.flush()
    db_session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}))
    db_session.commit()
    db_session.refresh(ws)
    wid = ws.workspace_id

    r = client.get(f"/workspaces/{wid}/access", headers=_auth(token))
    assert r.status_code == status.HTTP_409_CONFLICT
    assert "not ready for access" in r.json()["detail"]
