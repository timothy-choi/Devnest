"""Unit tests for workspace HTTP routes (TestClient + dependency overrides, no orchestrator)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.libs.db.database import get_db
from app.services.auth_service.api.dependencies import get_current_user
from app.services.auth_service.models import UserAuth
from app.services.workspace_service.api.routers.workspaces import router
from app.services.workspace_service.api.schemas import CreateWorkspaceRequest
from app.services.workspace_service.services import workspace_intent_service


def _make_app(engine):
    app = FastAPI()
    app.include_router(router)

    def db_dep():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = db_dep
    return app


def test_post_workspaces_returns_202_and_payload_shape(
    workspace_unit_engine,
    owner_user_id: int,
) -> None:
    app = _make_app(workspace_unit_engine)

    def user_dep():
        return UserAuth(
            user_auth_id=owner_user_id,
            username="ws_unit_owner",
            email="ws_unit_owner@example.com",
            password_hash="x",
        )

    app.dependency_overrides[get_current_user] = user_dep

    with TestClient(app) as client:
        res = client.post(
            "/workspaces",
            json={
                "name": "My Workspace",
                "description": "test workspace",
                "is_private": True,
                "runtime": {
                    "image": "ghcr.io/example/ws:1",
                    "cpu_limit_cores": 1.5,
                    "memory_limit_mib": 2048,
                    "env": {"LOG_LEVEL": "info"},
                    "ports": [{"container_port": 8080, "host_port": 9080}],
                    "topology_id": 7,
                    "storage": {"ephemeral_gib": 5},
                },
            },
        )

    assert res.status_code == 202
    data = res.json()
    assert data["status"] == "CREATING"
    assert data["config_version"] == 1
    assert "workspace_id" in data
    assert "job_id" in data
    assert data["message"] == "Workspace creation accepted."

    with Session(workspace_unit_engine) as session:
        out = workspace_intent_service.get_workspace(
            session,
            workspace_id=data["workspace_id"],
            owner_user_id=owner_user_id,
        )
    assert out is not None
    assert out.name == "My Workspace"


def test_post_workspaces_validation_error_422(workspace_unit_engine, owner_user_id: int) -> None:
    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: UserAuth(
        user_auth_id=owner_user_id,
        username="ws_unit_owner",
        email="ws_unit_owner@example.com",
        password_hash="x",
    )

    with TestClient(app) as client:
        res = client.post("/workspaces", json={"description": "missing name"})

    assert res.status_code == 422


def test_get_workspaces_returns_list_shape(workspace_unit_engine, owner_user_id: int) -> None:
    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: UserAuth(
        user_auth_id=owner_user_id,
        username="ws_unit_owner",
        email="ws_unit_owner@example.com",
        password_hash="x",
    )

    with Session(workspace_unit_engine) as session:
        workspace_intent_service.create_workspace(
            session,
            owner_user_id=owner_user_id,
            body=CreateWorkspaceRequest(name="Listed WS"),
        )

    with TestClient(app) as client:
        res = client.get("/workspaces")

    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["name"] == "Listed WS"
    assert body["items"][0]["status"] == "CREATING"


def test_get_workspace_by_id_returns_200(workspace_unit_engine, owner_user_id: int) -> None:
    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: UserAuth(
        user_auth_id=owner_user_id,
        username="ws_unit_owner",
        email="ws_unit_owner@example.com",
        password_hash="x",
    )

    with Session(workspace_unit_engine) as session:
        created = workspace_intent_service.create_workspace(
            session,
            owner_user_id=owner_user_id,
            body=CreateWorkspaceRequest(name="Detail WS"),
        )
        wid = created.workspace_id

    with TestClient(app) as client:
        res = client.get(f"/workspaces/{wid}")

    assert res.status_code == 200
    detail = res.json()
    assert detail["workspace_id"] == wid
    assert detail["name"] == "Detail WS"
    assert detail["latest_config_version"] == 1


def test_get_workspace_by_id_not_found_404(workspace_unit_engine, owner_user_id: int) -> None:
    app = _make_app(workspace_unit_engine)
    app.dependency_overrides[get_current_user] = lambda: UserAuth(
        user_auth_id=owner_user_id,
        username="ws_unit_owner",
        email="ws_unit_owner@example.com",
        password_hash="x",
    )

    with TestClient(app) as client:
        res = client.get("/workspaces/999999")

    assert res.status_code == 404
    assert res.json()["detail"] == "Workspace not found"
