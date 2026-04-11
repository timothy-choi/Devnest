"""Unit tests: GET /workspaces/{id}/events (SSE) with TestClient + SQLite."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.libs.db.database import get_db
from app.services.auth_service.api.dependencies import get_current_user
from app.services.auth_service.models import UserAuth
from app.services.workspace_service.api.routers import workspaces as workspaces_router
from app.services.workspace_service.api.routers.workspaces import router
from app.services.workspace_service.models import Workspace


def _make_app(engine):
    app = FastAPI()
    app.include_router(router)

    def db_dep():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = db_dep
    return app


def _auth_user(owner_user_id: int) -> UserAuth:
    return UserAuth(
        user_auth_id=owner_user_id,
        username="ws_unit_owner",
        email="ws_unit_owner@example.com",
        password_hash="x",
    )


def _seed_workspace(session: Session, owner_id: int) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="SSE WS",
        owner_user_id=owner_id,
        status="RUNNING",
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.commit()
    session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id




def test_get_workspace_events_404_missing_workspace(
    workspace_unit_engine,
    owner_user_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspaces_router, "get_engine", lambda: workspace_unit_engine)
    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: _auth_user(owner_user_id)

    with TestClient(app) as client:
        res = client.get("/workspaces/999999/events")

    assert res.status_code == 404
    assert res.json()["detail"] == "Workspace not found"


def test_get_workspace_events_404_wrong_owner(
    workspace_unit_engine,
    owner_user_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspaces_router, "get_engine", lambda: workspace_unit_engine)
    with Session(workspace_unit_engine) as session:
        other = UserAuth(
            username="sse_other",
            email="sse_other@example.com",
            password_hash="h",
        )
        session.add(other)
        session.commit()
        session.refresh(other)
        other_id = other.user_auth_id
        assert other_id is not None
        wid = _seed_workspace(session, owner_user_id)

    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: UserAuth(
        user_auth_id=other_id,
        username="sse_other",
        email="sse_other@example.com",
        password_hash="h",
    )

    with TestClient(app) as client:
        res = client.get(f"/workspaces/{wid}/events")

    assert res.status_code == 404


def test_get_workspace_events_route_registered_get_only(workspace_unit_engine) -> None:
    """SSE streams are infinite; full stream behavior belongs in integration tests."""
    app = _make_app(workspace_unit_engine)
    matches = [
        r
        for r in app.router.routes
        if getattr(r, "path", None) == "/workspaces/{workspace_id}/events"
    ]
    assert len(matches) == 1
    route = matches[0]
    assert route.methods == {"GET"}
