"""Integration tests: workspace intent HTTP routes on PostgreSQL (real app + DB)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import status
from sqlalchemy import func
from sqlmodel import Session, select

from app.services.auth_service.services.auth_token import create_access_token
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceJob,
    WorkspaceStatus,
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


def _job_count_for_workspace(db_session, wid: int) -> int:
    stmt = select(func.count()).select_from(WorkspaceJob).where(WorkspaceJob.workspace_id == wid)
    return int(db_session.exec(stmt).one())


def _total_job_count(db_session) -> int:
    return int(db_session.exec(select(func.count()).select_from(WorkspaceJob)).one())


def _seed_workspace(
    db_session: Session,
    owner_id: int,
    *,
    status: str,
    num_configs: int = 1,
) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="Route Intent WS",
        description="integration",
        owner_user_id=owner_id,
        status=status,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(ws)
    db_session.flush()
    for v in range(1, num_configs + 1):
        db_session.add(
            WorkspaceConfig(
                workspace_id=ws.workspace_id,
                version=v,
                config_json={"v": v},
            )
        )
    db_session.commit()
    db_session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


def test_post_start_202_persists_starting_and_start_job(client, db_session) -> None:
    uid, token = _register_and_token(client, username="int_start", email="int_start@example.com")
    wid = _seed_workspace(db_session, uid, status=WorkspaceStatus.STOPPED.value)
    before = _job_count_for_workspace(db_session, wid)

    r = client.post(f"/workspaces/start/{wid}", headers=_auth(token))
    assert r.status_code == status.HTTP_202_ACCEPTED, r.text
    data = r.json()
    assert data["workspace_id"] == wid
    assert data["status"] == WorkspaceStatus.STARTING.value
    assert data["job_type"] == "START"
    assert data["requested_config_version"] == 1
    assert data["issues"] == []

    ws = db_session.get(Workspace, wid)
    assert ws is not None
    assert ws.status == WorkspaceStatus.STARTING.value
    job = db_session.get(WorkspaceJob, data["job_id"])
    assert job is not None
    assert job.job_type == "START"
    assert job.status == "QUEUED"
    assert _job_count_for_workspace(db_session, wid) == before + 1


def test_post_stop_202_persists_stopping_job(client, db_session) -> None:
    uid, token = _register_and_token(client, username="int_stop", email="int_stop@example.com")
    wid = _seed_workspace(db_session, uid, status=WorkspaceStatus.RUNNING.value)

    r = client.post(f"/workspaces/stop/{wid}", headers=_auth(token))
    assert r.status_code == status.HTTP_202_ACCEPTED, r.text
    assert r.json()["status"] == WorkspaceStatus.STOPPING.value
    assert r.json()["job_type"] == "STOP"
    ws = db_session.get(Workspace, wid)
    assert ws is not None
    assert ws.status == WorkspaceStatus.STOPPING.value


def test_post_restart_202_persists_restarting_job(client, db_session) -> None:
    uid, token = _register_and_token(client, username="int_restart", email="int_restart@example.com")
    wid = _seed_workspace(db_session, uid, status=WorkspaceStatus.STOPPED.value)

    r = client.post(f"/workspaces/restart/{wid}", headers=_auth(token))
    assert r.status_code == status.HTTP_202_ACCEPTED, r.text
    assert r.json()["status"] == WorkspaceStatus.RESTARTING.value
    assert r.json()["job_type"] == "RESTART"


def test_delete_workspace_202_persists_deleting_job(client, db_session) -> None:
    uid, token = _register_and_token(client, username="int_del", email="int_del@example.com")
    wid = _seed_workspace(db_session, uid, status=WorkspaceStatus.RUNNING.value)

    r = client.delete(f"/workspaces/{wid}", headers=_auth(token))
    assert r.status_code == status.HTTP_202_ACCEPTED, r.text
    assert r.json()["status"] == WorkspaceStatus.DELETING.value
    assert r.json()["job_type"] == "DELETE"


def test_patch_update_202_persists_updating_config_v2_and_job(client, db_session) -> None:
    uid, token = _register_and_token(client, username="int_patch", email="int_patch@example.com")
    wid = _seed_workspace(db_session, uid, status=WorkspaceStatus.RUNNING.value, num_configs=1)

    r = client.patch(
        f"/workspaces/{wid}",
        headers=_auth(token),
        json={"runtime": {"image": "ghcr.io/int-patch:2", "cpu_limit_cores": 2.0}},
    )
    assert r.status_code == status.HTTP_202_ACCEPTED, r.text
    body = r.json()
    assert body["status"] == WorkspaceStatus.UPDATING.value
    assert body["job_type"] == "UPDATE"
    assert body["requested_config_version"] == 2

    job = db_session.get(WorkspaceJob, body["job_id"])
    assert job is not None
    assert job.requested_config_version == 2
    cfg2 = db_session.exec(
        select(WorkspaceConfig).where(
            WorkspaceConfig.workspace_id == wid,
            WorkspaceConfig.version == 2,
        )
    ).first()
    assert cfg2 is not None
    assert cfg2.config_json["image"] == "ghcr.io/int-patch:2"


def test_post_start_404_missing_workspace_no_new_global_jobs(client, db_session) -> None:
    _, token = _register_and_token(client, username="int_404", email="int_404@example.com")
    before = _total_job_count(db_session)

    r = client.post("/workspaces/start/888888888", headers=_auth(token))
    assert r.status_code == status.HTTP_404_NOT_FOUND
    assert _total_job_count(db_session) == before


def test_post_start_409_invalid_state_running(client, db_session) -> None:
    uid, token = _register_and_token(client, username="int_409_st", email="int_409_st@example.com")
    wid = _seed_workspace(db_session, uid, status=WorkspaceStatus.RUNNING.value)
    before = _job_count_for_workspace(db_session, wid)

    r = client.post(f"/workspaces/start/{wid}", headers=_auth(token))
    assert r.status_code == status.HTTP_409_CONFLICT
    assert "Start is only allowed" in r.json()["detail"]
    assert _job_count_for_workspace(db_session, wid) == before


def test_post_stop_409_busy_starting(client, db_session) -> None:
    uid, token = _register_and_token(client, username="int_busy", email="int_busy@example.com")
    wid = _seed_workspace(db_session, uid, status=WorkspaceStatus.STARTING.value)
    before = _job_count_for_workspace(db_session, wid)

    r = client.post(f"/workspaces/stop/{wid}", headers=_auth(token))
    assert r.status_code == status.HTTP_409_CONFLICT
    assert "busy" in r.json()["detail"].lower()
    assert _job_count_for_workspace(db_session, wid) == before
    ws = db_session.get(Workspace, wid)
    assert ws is not None
    assert ws.status == WorkspaceStatus.STARTING.value


def test_patch_update_422_missing_runtime(client, db_session) -> None:
    uid, token = _register_and_token(client, username="int_422", email="int_422@example.com")
    wid = _seed_workspace(db_session, uid, status=WorkspaceStatus.RUNNING.value)
    before = _job_count_for_workspace(db_session, wid)

    r = client.patch(f"/workspaces/{wid}", headers=_auth(token), json={})
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert _job_count_for_workspace(db_session, wid) == before
