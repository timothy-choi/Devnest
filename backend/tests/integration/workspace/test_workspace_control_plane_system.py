"""End-to-end workspace control-plane tests (API → job → worker → orchestrator → DB).

Lives under ``tests/integration/workspace/`` so PostgreSQL + ``TestClient`` fixtures come from
``tests/integration/conftest.py`` without forbidden nested ``pytest_plugins``.

Uses real FastAPI routes, Docker runtime, ``DbTopologyAdapter``, ``DefaultProbeRunner`` (TCP
connect stubbed via ``e2e_probe_socket_patch`` in this package's ``conftest.py``).

Markers:
- ``integration`` — participates in the integration CI job (Postgres).
- ``system`` — requires Docker; use ``-m "not system"`` only if you intentionally skip these.

Failure injection mocks the orchestrator factory; ``success=False`` roll-ups stay in unit tests.
"""

from __future__ import annotations

import os
import time
import uuid
from unittest.mock import create_autospec

import pytest
from fastapi import status
from sqlmodel import Session, select

from app.services.auth_service.services.auth_token import create_access_token
from app.services.orchestrator_service.errors import WorkspaceBringUpError
from app.services.orchestrator_service.interfaces import OrchestratorService
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceJob,
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceRuntime,
    WorkspaceRuntimeHealthStatus,
    WorkspaceStatus,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.system,
    pytest.mark.usefixtures(
        "docker_client",
        "_workspace_control_plane_env",
        "orchestrator_topology",
        "e2e_probe_socket_patch",
    ),
]


def _internal_headers() -> dict[str, str]:
    key = os.environ.get("INTERNAL_API_KEY", "")
    assert key, "INTERNAL_API_KEY must be set (integration conftest autouse)"
    return {"X-Internal-API-Key": key}


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_and_token(client, *, username: str, email: str) -> str:
    r = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": "SecurePass123!"},
    )
    assert r.status_code == status.HTTP_201_CREATED, r.text
    uid = r.json()["user_auth_id"]
    return create_access_token(user_id=uid)


def _create_workspace(client, token: str, *, name: str | None = None) -> tuple[int, int]:
    r = client.post(
        "/workspaces",
        json={
            "name": name or f"cp-{uuid.uuid4().hex[:10]}",
            "description": "control-plane E2E test",
            "is_private": True,
        },
        headers=_auth_header(token),
    )
    assert r.status_code == status.HTTP_202_ACCEPTED, r.text
    data = r.json()
    return int(data["workspace_id"]), int(data["job_id"])


def _process_job_to_workspace_status(
    client,
    db_session: Session,
    workspace_id: int,
    job_id: int,
    *,
    expected_workspace_status: str,
    timeout_s: float = 120.0,
) -> None:
    """Drive ``job_id`` until it finishes and the workspace reaches ``expected_workspace_status``.

    A single ``/process`` tick can leave the workspace in ``CREATING`` when a probe fails once and
    the worker schedules a bounded retry (still ``processed_count == 1`` for that tick).
    """
    deadline = time.monotonic() + timeout_s
    saw_process = False
    while time.monotonic() < deadline:
        r = client.post(
            "/internal/workspace-jobs/process",
            params={"job_id": job_id},
            headers=_internal_headers(),
        )
        assert r.status_code == status.HTTP_200_OK, r.text
        body = r.json()
        if body["processed_count"] == 1:
            assert body["last_job_id"] == job_id
            saw_process = True
        job = _reload_job(db_session, job_id)
        ws = _reload_workspace(db_session, workspace_id)
        if job is not None and job.status == WorkspaceJobStatus.FAILED.value:
            pytest.fail(job.error_msg or "workspace job failed")
        if job is not None and job.status == WorkspaceJobStatus.SUCCEEDED.value:
            assert ws is not None and ws.status == expected_workspace_status
            assert saw_process, "job succeeded without any processed tick"
            return
        time.sleep(0.15)
    pytest.fail(
        f"timeout waiting for job {job_id} to succeed "
        f"and workspace {workspace_id} to reach {expected_workspace_status!r}",
    )


def _reload_workspace(db_session: Session, workspace_id: int) -> Workspace | None:
    db_session.expire_all()
    return db_session.get(Workspace, workspace_id)


def _reload_job(db_session: Session, job_id: int) -> WorkspaceJob | None:
    db_session.expire_all()
    return db_session.get(WorkspaceJob, job_id)


def _runtime_for(db_session: Session, workspace_id: int) -> WorkspaceRuntime | None:
    db_session.expire_all()
    return db_session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id)).first()


def test_create_workspace_provisions_runtime_end_to_end(client, db_session: Session) -> None:
    token = _register_and_token(
        client,
        username=f"cp_create_{uuid.uuid4().hex[:8]}",
        email=f"cp_create_{uuid.uuid4().hex[:8]}@example.com",
    )
    wid, jid = _create_workspace(client, token)

    ws = _reload_workspace(db_session, wid)
    job = _reload_job(db_session, jid)
    assert ws is not None and ws.status == WorkspaceStatus.CREATING.value
    assert job is not None and job.status == WorkspaceJobStatus.QUEUED.value
    assert job.job_type == WorkspaceJobType.CREATE.value

    cfg = db_session.exec(select(WorkspaceConfig).where(WorkspaceConfig.workspace_id == wid)).first()
    assert cfg is not None and cfg.version == 1

    _process_job_to_workspace_status(
        client,
        db_session,
        wid,
        jid,
        expected_workspace_status=WorkspaceStatus.RUNNING.value,
    )

    ws = _reload_workspace(db_session, wid)
    job = _reload_job(db_session, jid)
    rt = _runtime_for(db_session, wid)
    assert ws is not None
    assert ws.status == WorkspaceStatus.RUNNING.value
    assert ws.last_error_code is None
    assert job is not None
    assert job.status == WorkspaceJobStatus.SUCCEEDED.value
    assert job.finished_at is not None
    assert job.error_msg is None
    assert rt is not None
    assert rt.node_id is not None
    assert rt.container_id
    assert rt.topology_id is not None
    assert rt.config_version == 1
    assert rt.internal_endpoint is not None
    assert rt.health_status == WorkspaceRuntimeHealthStatus.HEALTHY.value


def test_stop_start_and_restart_lifecycle_end_to_end(client, db_session: Session) -> None:
    token = _register_and_token(
        client,
        username=f"cp_life_{uuid.uuid4().hex[:8]}",
        email=f"cp_life_{uuid.uuid4().hex[:8]}@example.com",
    )
    wid, create_jid = _create_workspace(client, token)
    _process_job_to_workspace_status(
        client,
        db_session,
        wid,
        create_jid,
        expected_workspace_status=WorkspaceStatus.RUNNING.value,
    )
    assert _reload_workspace(db_session, wid).status == WorkspaceStatus.RUNNING.value

    r_stop = client.post(f"/workspaces/stop/{wid}", headers=_auth_header(token))
    assert r_stop.status_code == status.HTTP_202_ACCEPTED, r_stop.text
    stop_jid = int(r_stop.json()["job_id"])
    assert r_stop.json()["job_type"] == WorkspaceJobType.STOP.value
    _process_job_to_workspace_status(
        client,
        db_session,
        wid,
        stop_jid,
        expected_workspace_status=WorkspaceStatus.STOPPED.value,
    )
    ws = _reload_workspace(db_session, wid)
    job = _reload_job(db_session, stop_jid)
    assert ws.status == WorkspaceStatus.STOPPED.value
    assert job.status == WorkspaceJobStatus.SUCCEEDED.value

    r_start = client.post(f"/workspaces/start/{wid}", headers=_auth_header(token))
    assert r_start.status_code == status.HTTP_202_ACCEPTED, r_start.text
    start_jid = int(r_start.json()["job_id"])
    _process_job_to_workspace_status(
        client,
        db_session,
        wid,
        start_jid,
        expected_workspace_status=WorkspaceStatus.RUNNING.value,
    )
    ws = _reload_workspace(db_session, wid)
    assert ws.status == WorkspaceStatus.RUNNING.value
    assert _reload_job(db_session, start_jid).status == WorkspaceJobStatus.SUCCEEDED.value

    r_restart = client.post(f"/workspaces/restart/{wid}", headers=_auth_header(token))
    assert r_restart.status_code == status.HTTP_202_ACCEPTED, r_restart.text
    restart_jid = int(r_restart.json()["job_id"])
    assert r_restart.json()["job_type"] == WorkspaceJobType.RESTART.value
    _process_job_to_workspace_status(
        client,
        db_session,
        wid,
        restart_jid,
        expected_workspace_status=WorkspaceStatus.RUNNING.value,
    )
    ws = _reload_workspace(db_session, wid)
    job = _reload_job(db_session, restart_jid)
    assert ws.status == WorkspaceStatus.RUNNING.value
    assert job.status == WorkspaceJobStatus.SUCCEEDED.value


def test_update_workspace_config_end_to_end(client, db_session: Session) -> None:
    token = _register_and_token(
        client,
        username=f"cp_upd_{uuid.uuid4().hex[:8]}",
        email=f"cp_upd_{uuid.uuid4().hex[:8]}@example.com",
    )
    wid, create_jid = _create_workspace(client, token)
    _process_job_to_workspace_status(
        client,
        db_session,
        wid,
        create_jid,
        expected_workspace_status=WorkspaceStatus.RUNNING.value,
    )

    r_patch = client.patch(
        f"/workspaces/{wid}",
        json={"runtime": {"image": "nginx:alpine", "env": {"E2E_MARKER": "1"}}},
        headers=_auth_header(token),
    )
    assert r_patch.status_code == status.HTTP_202_ACCEPTED, r_patch.text
    assert r_patch.json()["requested_config_version"] == 2
    up_jid = int(r_patch.json()["job_id"])
    assert r_patch.json()["job_type"] == WorkspaceJobType.UPDATE.value

    _process_job_to_workspace_status(
        client,
        db_session,
        wid,
        up_jid,
        expected_workspace_status=WorkspaceStatus.RUNNING.value,
    )

    ws = _reload_workspace(db_session, wid)
    job = _reload_job(db_session, up_jid)
    cfg2 = db_session.exec(
        select(WorkspaceConfig).where(WorkspaceConfig.workspace_id == wid, WorkspaceConfig.version == 2),
    ).first()
    assert cfg2 is not None
    assert cfg2.config_json.get("env") == {"E2E_MARKER": "1"}
    assert ws.status == WorkspaceStatus.RUNNING.value
    assert job.status == WorkspaceJobStatus.SUCCEEDED.value
    rt = _runtime_for(db_session, wid)
    assert rt is not None and rt.config_version == 2


def test_delete_workspace_end_to_end(client, db_session: Session) -> None:
    token = _register_and_token(
        client,
        username=f"cp_del_{uuid.uuid4().hex[:8]}",
        email=f"cp_del_{uuid.uuid4().hex[:8]}@example.com",
    )
    wid, create_jid = _create_workspace(client, token)
    _process_job_to_workspace_status(
        client,
        db_session,
        wid,
        create_jid,
        expected_workspace_status=WorkspaceStatus.RUNNING.value,
    )

    r_del = client.delete(f"/workspaces/{wid}", headers=_auth_header(token))
    assert r_del.status_code == status.HTTP_202_ACCEPTED, r_del.text
    del_jid = int(r_del.json()["job_id"])
    assert r_del.json()["job_type"] == WorkspaceJobType.DELETE.value

    _process_job_to_workspace_status(
        client,
        db_session,
        wid,
        del_jid,
        expected_workspace_status=WorkspaceStatus.DELETED.value,
    )

    ws = _reload_workspace(db_session, wid)
    job = _reload_job(db_session, del_jid)
    rt = _runtime_for(db_session, wid)
    assert ws.status == WorkspaceStatus.DELETED.value
    assert job.status == WorkspaceJobStatus.SUCCEEDED.value
    assert rt is not None
    assert rt.container_id is None
    assert rt.container_state == "deleted"


def test_injected_bringup_failure_marks_job_and_workspace_error(
    client,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _register_and_token(
        client,
        username=f"cp_fail_{uuid.uuid4().hex[:8]}",
        email=f"cp_fail_{uuid.uuid4().hex[:8]}@example.com",
    )
    wid, jid = _create_workspace(client, token)

    job_pre = db_session.get(WorkspaceJob, jid)
    assert job_pre is not None
    job_pre.max_attempts = 1
    db_session.add(job_pre)
    db_session.commit()

    orch = create_autospec(OrchestratorService, instance=True)
    orch.bring_up_workspace_runtime.side_effect = WorkspaceBringUpError("system-test-injected-bringup-failure")
    monkeypatch.setattr(
        "app.workers.workspace_job_runner.build_orchestrator_for_workspace_job",
        lambda _session, _ws, _job: orch,
    )

    r = client.post(
        "/internal/workspace-jobs/process",
        params={"job_id": jid},
        headers=_internal_headers(),
    )
    assert r.status_code == status.HTTP_200_OK, r.text
    assert r.json()["processed_count"] == 1

    ws = _reload_workspace(db_session, wid)
    job = _reload_job(db_session, jid)
    assert ws is not None
    assert ws.status == WorkspaceStatus.ERROR.value
    assert ws.last_error_code == "ORCHESTRATOR_EXCEPTION"
    assert ws.last_error_message is not None
    assert job is not None
    assert job.status == WorkspaceJobStatus.FAILED.value
    assert job.error_msg is not None
    assert "system-test-injected-bringup-failure" in job.error_msg
