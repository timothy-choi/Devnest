"""Integration tests: snapshot HTTP routes (PostgreSQL + TestClient)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import status

from app.services.auth_service.services.auth_token import create_access_token
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceRuntime,
    WorkspaceRuntimeHealthStatus,
    WorkspaceStatus,
)

pytestmark = pytest.mark.integration


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


def _seed_running_workspace(db_session, owner_id: int) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="api-snap-ws",
        description="",
        owner_user_id=owner_id,
        status=WorkspaceStatus.RUNNING.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(ws)
    db_session.flush()
    db_session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}))
    db_session.add(
        WorkspaceRuntime(
            workspace_id=ws.workspace_id,
            node_id="node-api",
            container_id="c1",
            container_state="running",
            topology_id=1,
            internal_endpoint="http://10.0.0.2:8080",
            config_version=1,
            health_status=WorkspaceRuntimeHealthStatus.HEALTHY.value,
        ),
    )
    db_session.commit()
    db_session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


def test_snapshot_routes_require_auth(client) -> None:
    r = client.get("/workspaces/1/snapshots")
    assert r.status_code == status.HTTP_401_UNAUTHORIZED
    r2 = client.get("/snapshots/1")
    assert r2.status_code == status.HTTP_401_UNAUTHORIZED


def test_post_and_list_snapshots_202_and_200(client, db_session) -> None:
    uid, token = _register_and_token(
        client,
        username="snap_api_user",
        email="snap_api_user@example.com",
    )
    wid = _seed_running_workspace(db_session, uid)

    r = client.post(
        f"/workspaces/{wid}/snapshots",
        json={"name": "api-snap", "description": "via http", "metadata": {"tier": "dev"}},
        headers=_auth(token),
    )
    assert r.status_code == status.HTTP_202_ACCEPTED, r.text
    body = r.json()
    assert body["workspace_id"] == wid
    assert body["job_id"] > 0
    assert body["snapshot_id"] > 0
    assert body["status"] == "CREATING"

    r2 = client.get(f"/workspaces/{wid}/snapshots", headers=_auth(token))
    assert r2.status_code == status.HTTP_200_OK
    lst = r2.json()
    assert len(lst) == 1
    assert lst[0]["name"] == "api-snap"
    assert lst[0]["metadata"] == {"tier": "dev"}

    sid = body["snapshot_id"]
    r3 = client.get(f"/snapshots/{sid}", headers=_auth(token))
    assert r3.status_code == status.HTTP_200_OK
    assert r3.json()["workspace_snapshot_id"] == sid
