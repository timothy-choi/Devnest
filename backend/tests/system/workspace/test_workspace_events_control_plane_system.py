"""System tests: control-plane actions → persisted workspace events (same path as GET /workspaces/{id}/events SSE).

Exercises real API routes, ``POST /internal/workspace-jobs/process``, worker, orchestrator, Docker
runtime (``nginx:alpine``), topology DB row, and stubbed TCP probes — matching
``tests/integration/workspace/test_workspace_control_plane_system.py``.

**Observability:** The live SSE endpoint streams rows from ``list_workspace_events`` formatted with
``format_sse_data_line`` (see ``stream_workspace_events``). Integration coverage avoids opening an
infinite ``StreamingResponse`` in CI; here we assert the same persisted payloads + wire encoding
those clients would receive.

Markers:
- ``system`` — Docker + Postgres (``tests/system`` job in CI).
- Unmarked for slow/failure_path so merge-time ``tests/system`` tier runs these by default.

Restart is included in the stop/start lifecycle test (same worker batch as integration control-plane).
"""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import create_autospec

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

from app.services.auth_service.services.auth_token import create_access_token
from app.services.orchestrator_service.errors import WorkspaceBringUpError
from app.services.orchestrator_service.interfaces import OrchestratorService
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceEvent,
    WorkspaceJob,
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceRuntime,
    WorkspaceRuntimeHealthStatus,
    WorkspaceStatus,
)
from app.services.workspace_service.services.workspace_event_service import (
    WorkspaceStreamEventType,
    event_to_sse_dict,
    format_sse_data_line,
    list_workspace_events,
)

pytestmark = [
    pytest.mark.system,
    pytest.mark.usefixtures(
        "docker_client",
        "_workspace_control_plane_env",
        "workspace_control_plane_topology",
        "workspace_control_plane_probe_socket_patch",
    ),
]


@pytest.fixture(autouse=True)
def _truncate_workspace_events_system_tables(workspace_control_plane_postgres_engine: Engine) -> None:
    """Isolate each test; only this module registers this autouse."""
    with workspace_control_plane_postgres_engine.connect() as conn:
        for table in reversed(SQLModel.metadata.sorted_tables):
            conn.execute(text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE'))
        conn.commit()
    yield


@pytest.fixture(autouse=True)
def _workspace_events_internal_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "system-workspace-events-internal-key")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _internal_headers() -> dict[str, str]:
    key = os.environ.get("INTERNAL_API_KEY", "")
    assert key, "INTERNAL_API_KEY must be set"
    return {"X-Internal-API-Key": key}


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_and_token(client: TestClient, *, username: str, email: str) -> tuple[int, str]:
    r = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": "SecurePass123!"},
    )
    assert r.status_code == status.HTTP_201_CREATED, r.text
    uid = r.json()["user_auth_id"]
    return uid, create_access_token(user_id=uid)


def _create_workspace(client: TestClient, token: str, *, name: str | None = None) -> tuple[int, int]:
    r = client.post(
        "/workspaces",
        json={
            "name": name or f"sysev-{uuid.uuid4().hex[:10]}",
            "description": "system events E2E",
            "is_private": True,
        },
        headers=_auth_header(token),
    )
    assert r.status_code == status.HTTP_202_ACCEPTED, r.text
    data = r.json()
    return int(data["workspace_id"]), int(data["job_id"])


def _process_job(client: TestClient, job_id: int) -> None:
    r = client.post(
        "/internal/workspace-jobs/process",
        params={"job_id": job_id},
        headers=_internal_headers(),
    )
    assert r.status_code == status.HTTP_200_OK, r.text
    body = r.json()
    assert body["processed_count"] == 1
    assert body["last_job_id"] == job_id


def _reload_workspace(db_session: Session, workspace_id: int) -> Workspace | None:
    db_session.expire_all()
    return db_session.get(Workspace, workspace_id)


def _reload_job(db_session: Session, job_id: int) -> WorkspaceJob | None:
    db_session.expire_all()
    return db_session.get(WorkspaceJob, job_id)


def _runtime_for(db_session: Session, workspace_id: int) -> WorkspaceRuntime | None:
    db_session.expire_all()
    return db_session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id)).first()


def _observed_event_payloads(db_session: Session, *, workspace_id: int, owner_user_id: int) -> list[dict]:
    rows = list_workspace_events(
        db_session,
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        after_id=0,
    )
    return [event_to_sse_dict(r) for r in rows]


def _events_for_job(payloads: list[dict], job_id: int) -> list[dict]:
    return [p for p in payloads if p.get("payload", {}).get("job_id") == job_id]


def _assert_job_event_arc(job_events: list[dict], *, expect_succeeded: bool) -> None:
    assert len(job_events) >= 3, job_events
    assert job_events[0]["event_type"] == WorkspaceStreamEventType.INTENT_QUEUED
    assert job_events[1]["event_type"] == WorkspaceStreamEventType.JOB_RUNNING
    if expect_succeeded:
        assert job_events[-1]["event_type"] == WorkspaceStreamEventType.JOB_SUCCEEDED
        assert job_events[-1]["payload"].get("workspace_status") is not None
    else:
        assert job_events[-1]["event_type"] == WorkspaceStreamEventType.JOB_FAILED


def test_create_workspace_events_end_to_end(client: TestClient, db_session: Session) -> None:
    uid, token = _register_and_token(
        client,
        username=f"sysev_cr_{uuid.uuid4().hex[:8]}",
        email=f"sysev_cr_{uuid.uuid4().hex[:8]}@example.com",
    )
    wid, jid = _create_workspace(client, token)

    ws = _reload_workspace(db_session, wid)
    job = _reload_job(db_session, jid)
    assert ws is not None and ws.status == WorkspaceStatus.CREATING.value
    assert job is not None and job.status == WorkspaceJobStatus.QUEUED.value

    ev_before = _observed_event_payloads(db_session, workspace_id=wid, owner_user_id=uid)
    assert len(ev_before) == 1
    assert ev_before[0]["event_type"] == WorkspaceStreamEventType.INTENT_QUEUED
    assert ev_before[0]["payload"]["job_id"] == jid
    assert ev_before[0]["payload"]["job_type"] == WorkspaceJobType.CREATE.value
    row0 = db_session.get(WorkspaceEvent, ev_before[0]["id"])
    assert row0 is not None
    line0 = format_sse_data_line(row0)
    assert line0.startswith("data: ")
    assert json.loads(line0.split("\n\n", 1)[0][len("data: ") :]) == ev_before[0]

    _process_job(client, jid)

    ws = _reload_workspace(db_session, wid)
    job = _reload_job(db_session, jid)
    rt = _runtime_for(db_session, wid)
    assert ws.status == WorkspaceStatus.RUNNING.value
    assert job.status == WorkspaceJobStatus.SUCCEEDED.value
    assert rt is not None
    assert rt.health_status == WorkspaceRuntimeHealthStatus.HEALTHY.value

    payloads = _observed_event_payloads(db_session, workspace_id=wid, owner_user_id=uid)
    arc = _events_for_job(payloads, jid)
    _assert_job_event_arc(arc, expect_succeeded=True)
    assert arc[-1]["payload"]["workspace_status"] == WorkspaceStatus.RUNNING.value
    assert arc[-1]["status"] == WorkspaceStatus.RUNNING.value


def test_stop_start_restart_workspace_events_end_to_end(client: TestClient, db_session: Session) -> None:
    uid, token = _register_and_token(
        client,
        username=f"sysev_lf_{uuid.uuid4().hex[:8]}",
        email=f"sysev_lf_{uuid.uuid4().hex[:8]}@example.com",
    )
    wid, create_jid = _create_workspace(client, token)
    _process_job(client, create_jid)
    assert _reload_workspace(db_session, wid).status == WorkspaceStatus.RUNNING.value

    r_stop = client.post(f"/workspaces/stop/{wid}", headers=_auth_header(token))
    assert r_stop.status_code == status.HTTP_202_ACCEPTED, r_stop.text
    stop_jid = int(r_stop.json()["job_id"])
    _process_job(client, stop_jid)
    assert _reload_workspace(db_session, wid).status == WorkspaceStatus.STOPPED.value

    r_start = client.post(f"/workspaces/start/{wid}", headers=_auth_header(token))
    assert r_start.status_code == status.HTTP_202_ACCEPTED, r_start.text
    start_jid = int(r_start.json()["job_id"])
    _process_job(client, start_jid)
    assert _reload_workspace(db_session, wid).status == WorkspaceStatus.RUNNING.value

    r_restart = client.post(f"/workspaces/restart/{wid}", headers=_auth_header(token))
    assert r_restart.status_code == status.HTTP_202_ACCEPTED, r_restart.text
    restart_jid = int(r_restart.json()["job_id"])
    _process_job(client, restart_jid)
    assert _reload_workspace(db_session, wid).status == WorkspaceStatus.RUNNING.value

    payloads = _observed_event_payloads(db_session, workspace_id=wid, owner_user_id=uid)
    _assert_job_event_arc(_events_for_job(payloads, create_jid), expect_succeeded=True)
    _assert_job_event_arc(_events_for_job(payloads, stop_jid), expect_succeeded=True)
    assert _events_for_job(payloads, stop_jid)[-1]["payload"]["workspace_status"] == WorkspaceStatus.STOPPED.value
    _assert_job_event_arc(_events_for_job(payloads, start_jid), expect_succeeded=True)
    _assert_job_event_arc(_events_for_job(payloads, restart_jid), expect_succeeded=True)

    event_ids = [p["id"] for p in payloads]
    assert event_ids == sorted(event_ids), "events should match DB append order (SSE replay order)"


def test_update_workspace_events_end_to_end(client: TestClient, db_session: Session) -> None:
    uid, token = _register_and_token(
        client,
        username=f"sysev_up_{uuid.uuid4().hex[:8]}",
        email=f"sysev_up_{uuid.uuid4().hex[:8]}@example.com",
    )
    wid, create_jid = _create_workspace(client, token)
    _process_job(client, create_jid)

    r_patch = client.patch(
        f"/workspaces/{wid}",
        json={"runtime": {"image": "nginx:alpine", "env": {"SYS_EV_MARKER": "1"}}},
        headers=_auth_header(token),
    )
    assert r_patch.status_code == status.HTTP_202_ACCEPTED, r_patch.text
    up_jid = int(r_patch.json()["job_id"])
    assert r_patch.json()["job_type"] == WorkspaceJobType.UPDATE.value
    _process_job(client, up_jid)

    ws = _reload_workspace(db_session, wid)
    assert ws.status == WorkspaceStatus.RUNNING.value
    assert _runtime_for(db_session, wid).config_version == 2

    payloads = _observed_event_payloads(db_session, workspace_id=wid, owner_user_id=uid)
    _assert_job_event_arc(_events_for_job(payloads, up_jid), expect_succeeded=True)
    assert _events_for_job(payloads, up_jid)[0]["payload"]["job_type"] == WorkspaceJobType.UPDATE.value


def test_delete_workspace_events_end_to_end(client: TestClient, db_session: Session) -> None:
    uid, token = _register_and_token(
        client,
        username=f"sysev_dl_{uuid.uuid4().hex[:8]}",
        email=f"sysev_dl_{uuid.uuid4().hex[:8]}@example.com",
    )
    wid, create_jid = _create_workspace(client, token)
    _process_job(client, create_jid)

    r_del = client.delete(f"/workspaces/{wid}", headers=_auth_header(token))
    assert r_del.status_code == status.HTTP_202_ACCEPTED, r_del.text
    del_jid = int(r_del.json()["job_id"])
    _process_job(client, del_jid)

    ws = _reload_workspace(db_session, wid)
    rt = _runtime_for(db_session, wid)
    assert ws.status == WorkspaceStatus.DELETED.value
    assert rt is not None and rt.container_state == "deleted"

    payloads = _observed_event_payloads(db_session, workspace_id=wid, owner_user_id=uid)
    _assert_job_event_arc(_events_for_job(payloads, del_jid), expect_succeeded=True)
    assert _events_for_job(payloads, del_jid)[-1]["payload"]["workspace_status"] == WorkspaceStatus.DELETED.value


def test_create_workspace_bringup_failure_emits_failed_job_event(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid, token = _register_and_token(
        client,
        username=f"sysev_fl_{uuid.uuid4().hex[:8]}",
        email=f"sysev_fl_{uuid.uuid4().hex[:8]}@example.com",
    )
    wid, jid = _create_workspace(client, token)

    job_row = db_session.get(WorkspaceJob, jid)
    assert job_row is not None
    job_row.max_attempts = 1
    db_session.add(job_row)
    db_session.commit()

    orch = create_autospec(OrchestratorService, instance=True)
    orch.bring_up_workspace_runtime.side_effect = WorkspaceBringUpError("system-events-injected-bringup-failure")
    monkeypatch.setattr(
        "app.workers.workspace_job_runner.build_orchestrator_for_workspace_job",
        lambda _session, _ws, _job: orch,
    )

    _process_job(client, jid)

    ws = _reload_workspace(db_session, wid)
    job = _reload_job(db_session, jid)
    assert ws.status == WorkspaceStatus.ERROR.value
    assert job.status == WorkspaceJobStatus.FAILED.value

    payloads = _observed_event_payloads(db_session, workspace_id=wid, owner_user_id=uid)
    arc = _events_for_job(payloads, jid)
    _assert_job_event_arc(arc, expect_succeeded=False)
    assert "system-events-injected-bringup-failure" in (arc[-1]["payload"].get("error_msg") or "")

