"""Integration tests: workspace HTTP routes on PostgreSQL (real app, real ``get_db`` override)."""

from __future__ import annotations

from fastapi import status
from sqlalchemy import func
from sqlmodel import select

from app.services.auth_service.services.auth_token import create_access_token
from app.services.workspace_service.models import Workspace, WorkspaceConfig, WorkspaceJob


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


def _workspace_table_counts(db_session):
    w = int(db_session.exec(select(func.count()).select_from(Workspace)).one())
    c = int(db_session.exec(select(func.count()).select_from(WorkspaceConfig)).one())
    j = int(db_session.exec(select(func.count()).select_from(WorkspaceJob)).one())
    return w, c, j


def test_post_workspaces_202_persists_rows_and_matches_db(client, db_session) -> None:
    _, token = _register_and_token(client, username="ws_route_owner", email="ws_route_owner@example.com")

    payload = {
        "name": "My Workspace",
        "description": "integration test workspace",
        "is_private": True,
        "runtime": {
            "image": "ghcr.io/example/ws:int",
            "cpu_limit_cores": 1.5,
            "memory_limit_mib": 2048,
            "env": {"FOO": "bar"},
            "ports": [{"container_port": 8080, "host_port": 28080}],
            "topology_id": 55,
            "storage": {"class": "ssd"},
        },
    }
    r = client.post("/workspaces", json=payload, headers=_auth(token))
    assert r.status_code == status.HTTP_202_ACCEPTED, r.text
    data = r.json()
    assert data["status"] == "PENDING"
    assert data["config_version"] == 1
    assert "workspace_id" in data and "job_id" in data
    assert data["message"] == "Workspace creation accepted."

    wid = data["workspace_id"]
    jid = data["job_id"]

    ws = db_session.get(Workspace, wid)
    assert ws is not None
    assert ws.name == "My Workspace"
    assert ws.description == "integration test workspace"
    assert ws.status == "PENDING"
    assert ws.is_private is True

    cfg = db_session.exec(select(WorkspaceConfig).where(WorkspaceConfig.workspace_id == wid)).first()
    assert cfg is not None
    assert cfg.version == 1
    assert cfg.config_json["image"] == "ghcr.io/example/ws:int"
    assert cfg.config_json["cpu_limit_cores"] == 1.5
    assert cfg.config_json["memory_limit_mib"] == 2048
    assert cfg.config_json["env"] == {"FOO": "bar"}
    assert cfg.config_json["ports"] == [{"container_port": 8080, "host_port": 28080}]
    assert cfg.config_json["topology_id"] == 55
    assert cfg.config_json["storage"] == {"class": "ssd"}

    job = db_session.get(WorkspaceJob, jid)
    assert job is not None
    assert job.workspace_id == wid
    assert job.job_type == "CREATE"
    assert job.status == "QUEUED"
    assert job.requested_config_version == 1


def test_post_workspaces_invalid_body_422_and_no_workspace_rows(client, db_session) -> None:
    _, token = _register_and_token(client, username="ws_bad_body", email="ws_bad_body@example.com")
    assert _workspace_table_counts(db_session) == (0, 0, 0)

    r = client.post(
        "/workspaces",
        json={"description": "missing name"},
        headers=_auth(token),
    )
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert _workspace_table_counts(db_session) == (0, 0, 0)


def test_post_workspaces_unauthorized_without_token(client, db_session) -> None:
    assert _workspace_table_counts(db_session) == (0, 0, 0)
    r = client.post("/workspaces", json={"name": "X"})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED
    assert _workspace_table_counts(db_session) == (0, 0, 0)


def test_get_workspaces_empty_then_populated(client, db_session) -> None:
    _, token = _register_and_token(client, username="ws_list_user", email="ws_list_user@example.com")

    empty = client.get("/workspaces", headers=_auth(token))
    assert empty.status_code == status.HTTP_200_OK
    assert empty.json() == {"items": [], "total": 0}

    client.post(
        "/workspaces",
        json={"name": "Listed A", "description": "a"},
        headers=_auth(token),
    )
    client.post(
        "/workspaces",
        json={"name": "Listed B", "description": "b"},
        headers=_auth(token),
    )

    r = client.get("/workspaces", headers=_auth(token))
    assert r.status_code == status.HTTP_200_OK
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    names = {item["name"] for item in body["items"]}
    assert names == {"Listed A", "Listed B"}
    for item in body["items"]:
        assert item["status"] == "PENDING"
        assert "workspace_id" in item
        assert "is_private" in item
        assert "created_at" in item


def test_get_workspace_by_id_200_and_404(client, db_session) -> None:
    uid, token = _register_and_token(client, username="ws_get_one", email="ws_get_one@example.com")

    created = client.post(
        "/workspaces",
        json={"name": "Single WS", "description": "for get"},
        headers=_auth(token),
    )
    assert created.status_code == status.HTTP_202_ACCEPTED
    wid = created.json()["workspace_id"]

    ok = client.get(f"/workspaces/{wid}", headers=_auth(token))
    assert ok.status_code == status.HTTP_200_OK
    detail = ok.json()
    assert detail["workspace_id"] == wid
    assert detail["name"] == "Single WS"
    assert detail["description"] == "for get"
    assert detail["owner_user_id"] == uid
    assert detail["status"] == "PENDING"
    assert detail["latest_config_version"] == 1

    missing = client.get("/workspaces/999999999", headers=_auth(token))
    assert missing.status_code == status.HTTP_404_NOT_FOUND
    assert missing.json()["detail"] == "Workspace not found"
