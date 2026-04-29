"""System tests: stop/delete remove gateway routes (route-admin)."""

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
    WorkspaceStatus,
)

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
def _truncate_gateway_dereg_tables(workspace_control_plane_postgres_engine: Engine) -> None:
    helpers.clear_all_registered_routes()
    with workspace_control_plane_postgres_engine.connect() as conn:
        for table in reversed(SQLModel.metadata.sorted_tables):
            conn.execute(text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE'))
        conn.commit()
    yield


def test_stop_workspace_deregisters_gateway_route(
    workspace_control_plane_client: TestClient,
    workspace_control_plane_db_session: Session,
) -> None:
    client = workspace_control_plane_client
    db_session = workspace_control_plane_db_session
    uid, token = helpers.register_and_token(
        client,
        username=f"gw_stop_{uuid.uuid4().hex[:8]}",
        email=f"gw_stop_{uuid.uuid4().hex[:8]}@example.com",
    )
    wid, create_jid = helpers.create_workspace(client, token)
    helpers.process_job(client, create_jid)
    assert helpers.route_for_workspace(helpers.fetch_registered_routes(), wid) is not None

    r_stop = client.post(f"/workspaces/stop/{wid}", headers=helpers.auth_header(token))
    assert r_stop.status_code == status.HTTP_202_ACCEPTED, r_stop.text
    stop_jid = int(r_stop.json()["job_id"])
    helpers.process_job(client, stop_jid)

    ws = db_session.get(Workspace, wid)
    assert ws is not None and ws.status == WorkspaceStatus.STOPPED.value
    assert helpers.route_for_workspace(helpers.fetch_registered_routes(), wid) is None


def test_delete_workspace_deregisters_gateway_route(
    workspace_control_plane_client: TestClient,
    workspace_control_plane_db_session: Session,
) -> None:
    client = workspace_control_plane_client
    db_session = workspace_control_plane_db_session
    uid, token = helpers.register_and_token(
        client,
        username=f"gw_del_{uuid.uuid4().hex[:8]}",
        email=f"gw_del_{uuid.uuid4().hex[:8]}@example.com",
    )
    wid, create_jid = helpers.create_workspace(client, token)
    helpers.process_job(client, create_jid)
    assert helpers.route_for_workspace(helpers.fetch_registered_routes(), wid) is not None

    r_del = client.delete(f"/workspaces/{wid}", headers=helpers.auth_header(token))
    assert r_del.status_code == status.HTTP_202_ACCEPTED, r_del.text
    del_jid = int(r_del.json()["job_id"])
    helpers.process_job(client, del_jid)

    ws = db_session.get(Workspace, wid)
    assert ws is not None and ws.status == WorkspaceStatus.DELETED.value
    assert helpers.route_for_workspace(helpers.fetch_registered_routes(), wid) is None
