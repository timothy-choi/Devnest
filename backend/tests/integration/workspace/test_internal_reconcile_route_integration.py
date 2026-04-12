"""Integration tests: POST /internal/workspaces/{id}/reconcile-runtime."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from app.services.auth_service.models import UserAuth
from app.services.workspace_service.models import Workspace, WorkspaceConfig, WorkspaceJob, WorkspaceJobType, WorkspaceStatus

INTERNAL_HEADERS = {"X-Internal-API-Key": "integration-test-internal-key"}


def _seed_owner(session: Session) -> int:
    user = UserAuth(
        username="reconcile_route_owner",
        email="reconcile_route_owner@example.com",
        password_hash="x",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.user_auth_id is not None
    return user.user_auth_id


def _seed_running(session: Session, owner_id: int) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="reconcile-api-ws",
        description="",
        owner_user_id=owner_id,
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


def test_post_reconcile_runtime_returns_202_and_queues_job(
    client,
    db_session: Session,
) -> None:
    owner = _seed_owner(db_session)
    wid = _seed_running(db_session, owner)
    db_session.commit()

    r = client.post(f"/internal/workspaces/{wid}/reconcile-runtime", headers=INTERNAL_HEADERS)
    assert r.status_code == 202, r.text
    data = r.json()
    assert data["workspace_id"] == wid
    assert data["job_type"] == WorkspaceJobType.RECONCILE_RUNTIME.value
    assert data["status"] == WorkspaceStatus.RUNNING.value
    assert "job_id" in data

    job = db_session.exec(select(WorkspaceJob).where(WorkspaceJob.workspace_job_id == data["job_id"])).first()
    assert job is not None
    assert job.job_type == WorkspaceJobType.RECONCILE_RUNTIME.value


def test_post_reconcile_runtime_404(client, db_session: Session) -> None:
    db_session.commit()
    r = client.post("/internal/workspaces/999999/reconcile-runtime", headers=INTERNAL_HEADERS)
    assert r.status_code == 404
