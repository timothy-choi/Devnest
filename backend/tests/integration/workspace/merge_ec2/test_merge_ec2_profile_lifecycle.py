"""Merge-tier EC2/VM profile lifecycle (same coverage as slow EC2 E2E, Docker optional via local conftest).

Proves create → RUNNING (with HTTP probe path via workspace fixtures), stop → STOPPED, start again
→ RUNNING with persisted placement, delete → DELETED. Excluded only from the ``slow`` marker so it
runs in the default merge-time ``tests/integration`` selector when Docker is present.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi import status
from sqlmodel import Session, select

from app.services.auth_service.services.auth_token import create_access_token
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceCleanupTask,
    WorkspaceJob,
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceRuntime,
    WorkspaceStatus,
)
from app.services.workspace_service.models.enums import WorkspaceCleanupTaskStatus

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures(
        "docker_client",
        "_workspace_control_plane_env",
        "orchestrator_topology",
        "e2e_probe_socket_patch",
    ),
]


def _internal_headers() -> dict[str, str]:
    key = os.environ.get("INTERNAL_API_KEY", "")
    assert key
    return {"X-Internal-API-Key": key}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_and_token(client, *, username: str, email: str) -> str:
    r = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": "SecurePass123!"},
    )
    assert r.status_code == status.HTTP_201_CREATED, r.text
    uid = r.json()["user_auth_id"]
    return create_access_token(user_id=uid)


def _process_job(client, db_session: Session, job_id: int) -> None:
    r = client.post(
        "/internal/workspace-jobs/process",
        params={"job_id": job_id},
        headers=_internal_headers(),
    )
    assert r.status_code == status.HTTP_200_OK, r.text
    body = r.json()
    assert body["processed_count"] == 1
    db_session.expire_all()


def test_merge_ec2_profile_create_stop_start_delete_reuses_runtime_placement(
    client,
    db_session: Session,
) -> None:
    token = _register_and_token(
        client,
        username=f"merge_ec2_{uuid.uuid4().hex[:8]}",
        email=f"merge_ec2_{uuid.uuid4().hex[:8]}@example.com",
    )
    r = client.post(
        "/workspaces",
        json={"name": f"merge-ec2-{uuid.uuid4().hex[:10]}", "description": "merge ec2 proof", "is_private": True},
        headers=_auth(token),
    )
    assert r.status_code == status.HTTP_202_ACCEPTED, r.text
    wid = int(r.json()["workspace_id"])
    create_jid = int(r.json()["job_id"])
    _process_job(client, db_session, create_jid)
    ws = db_session.get(Workspace, wid)
    assert ws is not None
    assert ws.status == WorkspaceStatus.RUNNING.value
    rt_run = db_session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
    assert rt_run is not None and rt_run.node_id and rt_run.topology_id
    assert (rt_run.container_id or "").strip(), "authoritative engine id required after bring-up"
    debt = db_session.exec(
        select(WorkspaceCleanupTask).where(
            WorkspaceCleanupTask.workspace_id == wid,
            WorkspaceCleanupTask.status == WorkspaceCleanupTaskStatus.PENDING.value,
        ),
    ).first()
    assert debt is None, "no durable cleanup debt after successful bring-up"

    r_stop = client.post(f"/workspaces/stop/{wid}", headers=_auth(token))
    assert r_stop.status_code == status.HTTP_202_ACCEPTED, r_stop.text
    stop_jid = int(r_stop.json()["job_id"])
    _process_job(client, db_session, stop_jid)
    assert db_session.get(Workspace, wid).status == WorkspaceStatus.STOPPED.value

    r_start = client.post(f"/workspaces/start/{wid}", headers=_auth(token))
    assert r_start.status_code == status.HTTP_202_ACCEPTED, r_start.text
    start_jid = int(r_start.json()["job_id"])
    _process_job(client, db_session, start_jid)
    ws2 = db_session.get(Workspace, wid)
    assert ws2 is not None and ws2.status == WorkspaceStatus.RUNNING.value
    job_start = db_session.get(WorkspaceJob, start_jid)
    assert job_start is not None and job_start.status == WorkspaceJobStatus.SUCCEEDED.value
    rt_after = db_session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
    assert rt_after is not None
    assert rt_after.node_id == rt_run.node_id and rt_after.topology_id == rt_run.topology_id, (
        "placement should persist across stop/start in merge-tier EC2 profile path"
    )

    r_del = client.delete(f"/workspaces/{wid}", headers=_auth(token))
    assert r_del.status_code == status.HTTP_202_ACCEPTED, r_del.text
    del_jid = int(r_del.json()["job_id"])
    assert r_del.json()["job_type"] == WorkspaceJobType.DELETE.value
    _process_job(client, db_session, del_jid)
    ws3 = db_session.get(Workspace, wid)
    assert ws3 is not None and ws3.status == WorkspaceStatus.DELETED.value
