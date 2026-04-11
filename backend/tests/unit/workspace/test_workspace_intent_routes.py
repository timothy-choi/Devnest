"""Unit tests for workspace intent HTTP routes (TestClient + dependency overrides)."""

from __future__ import annotations

from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.libs.db.database import get_db
from app.services.auth_service.api.dependencies import get_current_user
from app.services.auth_service.models import UserAuth
from app.services.workspace_service.api.routers.workspaces import router
from app.services.workspace_service.models import WorkspaceStatus


def _make_app(engine):
    app = FastAPI()
    app.include_router(router)

    def db_dep():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = db_dep
    return app


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _user_dep(owner_user_id: int):
    return UserAuth(
        user_auth_id=owner_user_id,
        username="ws_unit_owner",
        email="ws_unit_owner@example.com",
        password_hash="x",
    )


def _seed_stopped_workspace(session: Session, owner_user_id: int) -> int:
    from datetime import datetime, timezone

    from app.services.workspace_service.models import Workspace, WorkspaceConfig

    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="Route WS",
        description="r",
        owner_user_id=owner_user_id,
        status=WorkspaceStatus.STOPPED.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.flush()
    session.add(
        WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={"k": 1}),
    )
    session.commit()
    session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


def _seed_running_workspace(session: Session, owner_user_id: int) -> int:
    from datetime import datetime, timezone

    from app.services.workspace_service.models import Workspace, WorkspaceConfig

    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="Run WS",
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
    assert ws.workspace_id is not None
    return ws.workspace_id


def test_post_start_returns_202(workspace_unit_engine, owner_user_id: int) -> None:
    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _user_dep(owner_user_id)

    with Session(workspace_unit_engine) as session:
        wid = _seed_stopped_workspace(session, owner_user_id)

    with TestClient(app) as client:
        r = client.post(f"/workspaces/start/{wid}", headers=_auth("dummy"))

    assert r.status_code == status.HTTP_202_ACCEPTED
    data = r.json()
    assert data["workspace_id"] == wid
    assert data["status"] == WorkspaceStatus.STARTING.value
    assert data["job_type"] == "START"
    assert data["requested_config_version"] == 1
    assert data["issues"] == []


def test_post_stop_returns_202(workspace_unit_engine, owner_user_id: int) -> None:
    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _user_dep(owner_user_id)

    with Session(workspace_unit_engine) as session:
        wid = _seed_running_workspace(session, owner_user_id)

    with TestClient(app) as client:
        r = client.post(f"/workspaces/stop/{wid}", headers=_auth("x"))

    assert r.status_code == status.HTTP_202_ACCEPTED
    assert r.json()["status"] == WorkspaceStatus.STOPPING.value
    assert r.json()["job_type"] == "STOP"


def test_post_restart_returns_202(workspace_unit_engine, owner_user_id: int) -> None:
    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _user_dep(owner_user_id)

    with Session(workspace_unit_engine) as session:
        wid = _seed_stopped_workspace(session, owner_user_id)

    with TestClient(app) as client:
        r = client.post(f"/workspaces/restart/{wid}", headers=_auth("x"))

    assert r.status_code == status.HTTP_202_ACCEPTED
    assert r.json()["status"] == WorkspaceStatus.RESTARTING.value
    assert r.json()["job_type"] == "RESTART"


def test_delete_workspace_returns_202(workspace_unit_engine, owner_user_id: int) -> None:
    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _user_dep(owner_user_id)

    with Session(workspace_unit_engine) as session:
        wid = _seed_running_workspace(session, owner_user_id)

    with TestClient(app) as client:
        r = client.delete(f"/workspaces/{wid}", headers=_auth("x"))

    assert r.status_code == status.HTTP_202_ACCEPTED
    assert r.json()["status"] == WorkspaceStatus.DELETING.value
    assert r.json()["job_type"] == "DELETE"


def test_patch_workspace_update_returns_202(workspace_unit_engine, owner_user_id: int) -> None:
    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _user_dep(owner_user_id)

    with Session(workspace_unit_engine) as session:
        wid = _seed_running_workspace(session, owner_user_id)

    with TestClient(app) as client:
        r = client.patch(
            f"/workspaces/{wid}",
            headers=_auth("x"),
            json={"runtime": {"image": "ghcr.io/patch:1", "cpu_limit_cores": 2.0}},
        )

    assert r.status_code == status.HTTP_202_ACCEPTED
    body = r.json()
    assert body["status"] == WorkspaceStatus.UPDATING.value
    assert body["job_type"] == "UPDATE"
    assert body["requested_config_version"] == 2


def test_intent_routes_404_not_found(workspace_unit_engine, owner_user_id: int) -> None:
    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _user_dep(owner_user_id)

    with TestClient(app) as client:
        r = client.post("/workspaces/start/999999", headers=_auth("x"))
    assert r.status_code == status.HTTP_404_NOT_FOUND


def test_intent_routes_409_invalid_state(workspace_unit_engine, owner_user_id: int) -> None:
    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _user_dep(owner_user_id)

    with Session(workspace_unit_engine) as session:
        wid = _seed_running_workspace(session, owner_user_id)

    with TestClient(app) as client:
        r = client.post(f"/workspaces/start/{wid}", headers=_auth("x"))
    assert r.status_code == status.HTTP_409_CONFLICT
    assert "Start is only allowed" in r.json()["detail"]


def test_intent_routes_409_busy(workspace_unit_engine, owner_user_id: int) -> None:
    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _user_dep(owner_user_id)

    from datetime import datetime, timezone

    from app.services.workspace_service.models import Workspace, WorkspaceConfig

    with Session(workspace_unit_engine) as session:
        now = datetime.now(timezone.utc)
        ws = Workspace(
            name="busy",
            owner_user_id=owner_user_id,
            status=WorkspaceStatus.STARTING.value,
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
        assert wid is not None

    with TestClient(app) as client:
        r = client.post(f"/workspaces/stop/{wid}", headers=_auth("x"))
    assert r.status_code == status.HTTP_409_CONFLICT
    assert "busy" in r.json()["detail"].lower()


def test_patch_update_validation_422_missing_runtime(workspace_unit_engine, owner_user_id: int) -> None:
    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _user_dep(owner_user_id)

    with Session(workspace_unit_engine) as session:
        wid = _seed_running_workspace(session, owner_user_id)

    with TestClient(app) as client:
        r = client.patch(f"/workspaces/{wid}", headers=_auth("x"), json={})
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
