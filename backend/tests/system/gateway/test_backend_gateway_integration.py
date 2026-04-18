"""System tests: control plane registers workspace routes with route-admin (real Docker + Postgres).

Markers: ``system``, ``gateway``, ``slow`` — run via CI job ``system-gateway-tests`` (not merge-time
``system-tests``, which excludes ``gateway``).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

from app.services.workspace_service.models import (
    Workspace,
    WorkspaceJob,
    WorkspaceJobStatus,
    WorkspaceRuntime,
    WorkspaceStatus,
)
from app.services.workspace_service.services.workspace_event_service import list_workspace_events

from . import helpers

pytestmark = [
    pytest.mark.system,
    pytest.mark.gateway,
    pytest.mark.slow,
    pytest.mark.usefixtures(
        "gateway_system_stack",
        "docker_client",
        "_workspace_control_plane_env",
        "workspace_control_plane_topology",
        "workspace_control_plane_probe_socket_patch",
    ),
]


@pytest.fixture(autouse=True)
def _truncate_gateway_integration_tables(workspace_control_plane_postgres_engine: Engine) -> None:
    # Route-admin state survives across tests; Postgres IDs restart at 1 after TRUNCATE — clear routes first.
    helpers.clear_all_registered_routes()
    with workspace_control_plane_postgres_engine.connect() as conn:
        for table in reversed(SQLModel.metadata.sorted_tables):
            conn.execute(text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE'))
        conn.commit()
    yield


def _norm_target(s: str | None) -> str:
    t = (s or "").strip()
    if not t:
        return ""
    if t.startswith("http://") or t.startswith("https://"):
        return t
    return f"http://{t}"


def test_create_running_workspace_registers_gateway_route(
    workspace_control_plane_client: TestClient,
    workspace_control_plane_db_session: Session,
) -> None:
    client = workspace_control_plane_client
    db_session = workspace_control_plane_db_session
    uid, token = helpers.register_and_token(
        client,
        username=f"gw_int_{uuid.uuid4().hex[:8]}",
        email=f"gw_int_{uuid.uuid4().hex[:8]}@example.com",
    )
    wid, jid = helpers.create_workspace(client, token)
    helpers.process_job(client, jid)

    ws = db_session.get(Workspace, wid)
    rt = db_session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
    assert ws is not None
    assert ws.status == WorkspaceStatus.RUNNING.value
    assert rt is not None
    assert (rt.internal_endpoint or "").strip()

    routes = helpers.fetch_registered_routes()
    row = helpers.route_for_workspace(routes, wid)
    assert row is not None, routes
    assert row["public_host"] == ws.public_host
    assert str(wid) == row["workspace_id"]
    assert _norm_target(rt.internal_endpoint) == _norm_target(row.get("target"))


def test_access_and_attach_return_public_host_and_gateway_url(
    workspace_control_plane_client: TestClient,
    workspace_control_plane_db_session: Session,
) -> None:
    client = workspace_control_plane_client
    db_session = workspace_control_plane_db_session
    uid, token = helpers.register_and_token(
        client,
        username=f"gw_acc_{uuid.uuid4().hex[:8]}",
        email=f"gw_acc_{uuid.uuid4().hex[:8]}@example.com",
    )
    wid, jid = helpers.create_workspace(client, token)
    helpers.process_job(client, jid)
    ws = db_session.get(Workspace, wid)
    assert ws is not None
    expected_host = ws.public_host
    assert expected_host

    r_att = client.post(f"/workspaces/attach/{wid}", headers=helpers.auth_header(token))
    assert r_att.status_code == status.HTTP_200_OK, r_att.text
    att = r_att.json()
    assert att["public_host"] == expected_host
    assert att["gateway_url"] == f"http://{expected_host}/"
    ws_tok = att["session_token"]

    r_acc = client.get(
        f"/workspaces/{wid}/access",
        headers=helpers.auth_and_workspace_session(token, ws_tok),
    )
    assert r_acc.status_code == status.HTTP_200_OK, r_acc.text
    acc = r_acc.json()
    assert acc["public_host"] == expected_host
    assert acc["gateway_url"] == f"http://{expected_host}/"


def test_workspace_job_events_persist_after_running_with_gateway(
    workspace_control_plane_client: TestClient,
    workspace_control_plane_db_session: Session,
) -> None:
    """Same event stream path as SSE clients (DB rows), without opening StreamingResponse."""
    client = workspace_control_plane_client
    db_session = workspace_control_plane_db_session
    uid, token = helpers.register_and_token(
        client,
        username=f"gw_ev_{uuid.uuid4().hex[:8]}",
        email=f"gw_ev_{uuid.uuid4().hex[:8]}@example.com",
    )
    wid, jid = helpers.create_workspace(client, token)
    helpers.process_job(client, jid)

    rows = list_workspace_events(
        db_session,
        workspace_id=wid,
        owner_user_id=uid,
        after_id=0,
    )
    assert len(rows) >= 3
    job_rows = [r for r in rows if (r.payload_json or {}).get("job_id") == jid]
    assert len(job_rows) >= 2


def test_bringup_failure_does_not_register_gateway_route(
    workspace_control_plane_client: TestClient,
    workspace_control_plane_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import create_autospec

    from app.services.orchestrator_service.errors import WorkspaceBringUpError
    from app.services.orchestrator_service.interfaces import OrchestratorService

    client = workspace_control_plane_client
    db_session = workspace_control_plane_db_session
    uid, token = helpers.register_and_token(
        client,
        username=f"gw_fail_{uuid.uuid4().hex[:8]}",
        email=f"gw_fail_{uuid.uuid4().hex[:8]}@example.com",
    )
    wid, jid = helpers.create_workspace(client, token)

    job_row = db_session.get(WorkspaceJob, jid)
    assert job_row is not None
    job_row.max_attempts = 1
    db_session.add(job_row)
    db_session.commit()

    orch = create_autospec(OrchestratorService, instance=True)
    orch.bring_up_workspace_runtime.side_effect = WorkspaceBringUpError(
        "gateway-system-injected-bringup-failure",
    )
    monkeypatch.setattr(
        "app.workers.workspace_job_runner.build_orchestrator_for_workspace_job",
        lambda _session, _ws, _job: orch,
    )

    helpers.process_job(client, jid)

    ws = db_session.get(Workspace, wid)
    job = db_session.get(WorkspaceJob, jid)
    assert ws is not None and ws.status == WorkspaceStatus.ERROR.value
    assert job is not None and job.status == WorkspaceJobStatus.FAILED.value

    routes = helpers.fetch_registered_routes()
    assert helpers.route_for_workspace(routes, wid) is None
