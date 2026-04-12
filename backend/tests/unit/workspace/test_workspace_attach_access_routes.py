"""Unit tests: workspace attach/access HTTP routes (TestClient + SQLite)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.libs.db.database import get_db
from app.services.auth_service.api.dependencies import get_current_user
from app.services.auth_service.models import UserAuth
from app.services.workspace_service.api.routers.workspaces import router
from app.services.workspace_service.models import Workspace, WorkspaceConfig, WorkspaceRuntime
from app.services.workspace_service.services.workspace_session_service import WORKSPACE_SESSION_HTTP_HEADER
from app.services.workspace_service.models.enums import (
    WorkspaceRuntimeHealthStatus,
    WorkspaceStatus,
)

ENDPOINT_REF = "node-route:32001"
PUBLIC_HOST = "ws-route.devnest.local"
INTERNAL_EP = "10.128.0.20:8080"
CONTAINER_ID = "ctr-route-xyz"


def _make_app(engine):
    app = FastAPI()
    app.include_router(router)

    def db_dep():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = db_dep
    return app


def _seed_ready_workspace(session: Session, owner_id: int) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="Route Attach WS",
        owner_user_id=owner_id,
        status=WorkspaceStatus.RUNNING.value,
        is_private=True,
        endpoint_ref=ENDPOINT_REF,
        public_host=PUBLIC_HOST,
        active_sessions_count=0,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.flush()
    session.add(
        WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}),
    )
    session.add(
        WorkspaceRuntime(
            workspace_id=ws.workspace_id,
            node_id="node-1",
            container_id=CONTAINER_ID,
            container_state="running",
            topology_id=1,
            internal_endpoint=INTERNAL_EP,
            config_version=1,
            health_status=WorkspaceRuntimeHealthStatus.HEALTHY.value,
        )
    )
    session.commit()
    session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


def _auth_user(owner_user_id: int):
    return UserAuth(
        user_auth_id=owner_user_id,
        username="ws_unit_owner",
        email="ws_unit_owner@example.com",
        password_hash="x",
    )


def _ws_session_headers(session_token: str) -> dict[str, str]:
    return {WORKSPACE_SESSION_HTTP_HEADER: session_token}


def test_post_attach_200_and_payload(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_ready_workspace(session, owner_user_id)

    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _auth_user(owner_user_id)

    with TestClient(app) as client:
        res = client.post(f"/workspaces/attach/{wid}")

    assert res.status_code == 200
    data = res.json()
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
    assert data["workspace_session_id"] >= 1
    assert data["session_token"].startswith("dnws_")
    assert data["session_expires_at"]


def test_get_access_200_and_payload(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_ready_workspace(session, owner_user_id)

    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _auth_user(owner_user_id)

    with TestClient(app) as client:
        att = client.post(f"/workspaces/attach/{wid}")
        assert att.status_code == 200, att.text
        tok = att.json()["session_token"]
        res = client.get(f"/workspaces/{wid}/access", headers=_ws_session_headers(tok))

    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["workspace_id"] == wid
    assert data["status"] == WorkspaceStatus.RUNNING.value
    assert data["runtime_ready"] is True
    assert data["endpoint_ref"] == ENDPOINT_REF
    assert data["public_host"] == PUBLIC_HOST
    assert data["internal_endpoint"] == INTERNAL_EP
    assert data["gateway_url"] is None
    assert data["issues"] == []

    with Session(workspace_unit_engine) as session:
        ws = session.get(Workspace, wid)
        assert ws is not None
        assert ws.active_sessions_count == 1


def test_get_access_403_without_workspace_session_header(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_ready_workspace(session, owner_user_id)

    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _auth_user(owner_user_id)

    with TestClient(app) as client:
        res = client.get(f"/workspaces/{wid}/access")

    assert res.status_code == 403
    assert "session token" in res.json()["detail"].lower()


def test_attach_not_found_404(workspace_unit_engine, owner_user_id: int) -> None:
    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _auth_user(owner_user_id)

    with TestClient(app) as client:
        res = client.post("/workspaces/attach/999999")

    assert res.status_code == 404
    assert res.json()["detail"] == "Workspace not found"


def test_access_not_found_404(workspace_unit_engine, owner_user_id: int) -> None:
    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _auth_user(owner_user_id)

    with TestClient(app) as client:
        res = client.get("/workspaces/999999/access")

    assert res.status_code == 404
    assert res.json()["detail"] == "Workspace not found"


def test_attach_not_ready_409(workspace_unit_engine, owner_user_id: int) -> None:
    now = datetime.now(timezone.utc)
    with Session(workspace_unit_engine) as session:
        ws = Workspace(
            name="Stopped",
            owner_user_id=owner_user_id,
            status=WorkspaceStatus.STOPPED.value,
            is_private=True,
            created_at=now,
            updated_at=now,
        )
        session.add(ws)
        session.flush()
        session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}))
        session.commit()
        session.refresh(ws)
        wid = ws.workspace_id

    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _auth_user(owner_user_id)

    with TestClient(app) as client:
        res = client.post(f"/workspaces/attach/{wid}")

    assert res.status_code == 409
    assert "RUNNING" in res.json()["detail"]


def test_access_not_ready_409(workspace_unit_engine, owner_user_id: int) -> None:
    now = datetime.now(timezone.utc)
    with Session(workspace_unit_engine) as session:
        ws = Workspace(
            name="Stopped",
            owner_user_id=owner_user_id,
            status=WorkspaceStatus.STOPPED.value,
            is_private=True,
            created_at=now,
            updated_at=now,
        )
        session.add(ws)
        session.flush()
        session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}))
        session.commit()
        session.refresh(ws)
        wid = ws.workspace_id

    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _auth_user(owner_user_id)

    with TestClient(app) as client:
        res = client.get(f"/workspaces/{wid}/access")

    assert res.status_code == 409


def test_attach_running_without_runtime_409(workspace_unit_engine, owner_user_id: int) -> None:
    now = datetime.now(timezone.utc)
    with Session(workspace_unit_engine) as session:
        ws = Workspace(
            name="RunningNoRt",
            owner_user_id=owner_user_id,
            status=WorkspaceStatus.RUNNING.value,
            is_private=True,
            created_at=now,
            updated_at=now,
        )
        session.add(ws)
        session.flush()
        session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}))
        session.commit()
        session.refresh(ws)
        wid = ws.workspace_id

    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _auth_user(owner_user_id)

    with TestClient(app) as client:
        res = client.post(f"/workspaces/attach/{wid}")

    assert res.status_code == 409
    assert "not ready for access" in res.json()["detail"]


def test_access_running_without_runtime_409(workspace_unit_engine, owner_user_id: int) -> None:
    now = datetime.now(timezone.utc)
    with Session(workspace_unit_engine) as session:
        ws = Workspace(
            name="RunningNoRt",
            owner_user_id=owner_user_id,
            status=WorkspaceStatus.RUNNING.value,
            is_private=True,
            created_at=now,
            updated_at=now,
        )
        session.add(ws)
        session.flush()
        session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}))
        session.commit()
        session.refresh(ws)
        wid = ws.workspace_id

    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _auth_user(owner_user_id)

    with TestClient(app) as client:
        res = client.get(f"/workspaces/{wid}/access")

    assert res.status_code == 409
    assert "not ready for access" in res.json()["detail"]
