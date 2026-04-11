"""Unit tests: workspace attach/access service (SQLite, no worker/orchestrator)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlmodel import Session

from app.services.auth_service.models import UserAuth
from app.services.workspace_service.errors import (
    WorkspaceBusyError,
    WorkspaceInvalidStateError,
    WorkspaceNotFoundError,
)
from app.services.workspace_service.models import Workspace, WorkspaceConfig, WorkspaceRuntime
from app.services.workspace_service.models.enums import (
    WorkspaceRuntimeHealthStatus,
    WorkspaceStatus,
)
from app.services.workspace_service.services import workspace_intent_service

ENDPOINT_REF = "node-prod-1:32001"
PUBLIC_HOST = "ws-123.devnest.local"
INTERNAL_EP = "10.128.0.10:8080"
CONTAINER_ID = "ctr-unit-abc123"


def _seed_running_with_runtime(
    session: Session,
    owner_id: int,
    *,
    endpoint_ref: str = ENDPOINT_REF,
    public_host: str = PUBLIC_HOST,
    internal_endpoint: str = INTERNAL_EP,
    container_id: str = CONTAINER_ID,
    health_status: str = WorkspaceRuntimeHealthStatus.HEALTHY.value,
    active_sessions_count: int = 0,
    name: str = "Attach Access WS",
) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name=name,
        description="unit",
        owner_user_id=owner_id,
        status=WorkspaceStatus.RUNNING.value,
        is_private=True,
        endpoint_ref=endpoint_ref,
        public_host=public_host,
        active_sessions_count=active_sessions_count,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.flush()
    session.add(
        WorkspaceConfig(
            workspace_id=ws.workspace_id,
            version=1,
            config_json={"marker": 1},
        )
    )
    session.add(
        WorkspaceRuntime(
            workspace_id=ws.workspace_id,
            node_id="node-prod-1",
            container_id=container_id,
            container_state="running",
            topology_id=42,
            internal_endpoint=internal_endpoint,
            config_version=1,
            health_status=health_status,
        )
    )
    session.commit()
    session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


def test_request_attach_happy_path(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_running_with_runtime(session, owner_user_id, active_sessions_count=0)
    with Session(workspace_unit_engine) as session:
        out = workspace_intent_service.request_attach_workspace(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            requested_by_user_id=owner_user_id,
        )

    assert out.accepted is True
    assert out.workspace_id == wid
    assert out.status == WorkspaceStatus.RUNNING.value
    assert out.runtime_ready is True
    assert out.endpoint_ref == ENDPOINT_REF
    assert out.public_host == PUBLIC_HOST
    assert out.internal_endpoint == INTERNAL_EP
    assert out.gateway_url is None
    assert out.issues == ()
    assert out.active_sessions_count == 1

    with Session(workspace_unit_engine) as session:
        ws = session.get(Workspace, wid)
        assert ws is not None
        assert ws.active_sessions_count == 1


def test_get_workspace_access_happy_path(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_running_with_runtime(session, owner_user_id, active_sessions_count=3)
    with Session(workspace_unit_engine) as session:
        out = workspace_intent_service.get_workspace_access(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
        )

    assert out.success is True
    assert out.workspace_id == wid
    assert out.status == WorkspaceStatus.RUNNING.value
    assert out.runtime_ready is True
    assert out.endpoint_ref == ENDPOINT_REF
    assert out.public_host == PUBLIC_HOST
    assert out.internal_endpoint == INTERNAL_EP
    assert out.gateway_url is None
    assert out.issues == ()

    with Session(workspace_unit_engine) as session:
        ws = session.get(Workspace, wid)
        assert ws is not None
        assert ws.active_sessions_count == 3


def test_get_workspace_access_does_not_increment_sessions(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_running_with_runtime(session, owner_user_id, active_sessions_count=5)
    with Session(workspace_unit_engine) as session:
        workspace_intent_service.get_workspace_access(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
        )
    with Session(workspace_unit_engine) as session:
        ws = session.get(Workspace, wid)
        assert ws is not None
        assert ws.active_sessions_count == 5


def test_attach_twice_increments_sessions(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_running_with_runtime(session, owner_user_id, active_sessions_count=0)
    with Session(workspace_unit_engine) as session:
        a = workspace_intent_service.request_attach_workspace(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            requested_by_user_id=owner_user_id,
        )
        b = workspace_intent_service.request_attach_workspace(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            requested_by_user_id=owner_user_id,
        )
    assert a.active_sessions_count == 1
    assert b.active_sessions_count == 2


@pytest.mark.parametrize(
    "status",
    [
        WorkspaceStatus.STOPPED.value,
        WorkspaceStatus.ERROR.value,
        WorkspaceStatus.DELETED.value,
    ],
)
def test_attach_rejected_when_not_running(
    workspace_unit_engine,
    owner_user_id: int,
    status: str,
) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_running_with_runtime(session, owner_user_id)
        ws = session.get(Workspace, wid)
        assert ws is not None
        ws.status = status
        session.add(ws)
        session.commit()

    with Session(workspace_unit_engine) as session:
        with pytest.raises(WorkspaceInvalidStateError, match="RUNNING"):
            workspace_intent_service.request_attach_workspace(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                requested_by_user_id=owner_user_id,
            )


def test_access_rejected_when_not_running(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_running_with_runtime(session, owner_user_id)
        ws = session.get(Workspace, wid)
        assert ws is not None
        ws.status = WorkspaceStatus.STOPPED.value
        session.add(ws)
        session.commit()

    with Session(workspace_unit_engine) as session:
        with pytest.raises(WorkspaceInvalidStateError, match="RUNNING"):
            workspace_intent_service.get_workspace_access(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
            )


@pytest.mark.parametrize(
    "busy_status",
    [
        WorkspaceStatus.CREATING.value,
        WorkspaceStatus.STARTING.value,
        WorkspaceStatus.STOPPING.value,
        WorkspaceStatus.RESTARTING.value,
        WorkspaceStatus.UPDATING.value,
        WorkspaceStatus.DELETING.value,
    ],
)
def test_attach_access_rejected_when_busy(
    workspace_unit_engine,
    owner_user_id: int,
    busy_status: str,
) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_running_with_runtime(session, owner_user_id)
        ws = session.get(Workspace, wid)
        assert ws is not None
        ws.status = busy_status
        session.add(ws)
        session.commit()

    with Session(workspace_unit_engine) as session:
        with pytest.raises(WorkspaceBusyError):
            workspace_intent_service.request_attach_workspace(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                requested_by_user_id=owner_user_id,
            )
        with pytest.raises(WorkspaceBusyError):
            workspace_intent_service.get_workspace_access(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
            )


def test_attach_access_running_without_runtime_row(workspace_unit_engine, owner_user_id: int) -> None:
    now = datetime.now(timezone.utc)
    with Session(workspace_unit_engine) as session:
        ws = Workspace(
            name="No RT",
            owner_user_id=owner_user_id,
            status=WorkspaceStatus.RUNNING.value,
            is_private=True,
            created_at=now,
            updated_at=now,
        )
        session.add(ws)
        session.flush()
        session.add(
            WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}),
        )
        session.commit()
        session.refresh(ws)
        wid = ws.workspace_id
    assert wid is not None

    with Session(workspace_unit_engine) as session:
        with pytest.raises(WorkspaceInvalidStateError, match="not ready for access"):
            workspace_intent_service.request_attach_workspace(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                requested_by_user_id=owner_user_id,
            )
        with pytest.raises(WorkspaceInvalidStateError, match="not ready for access"):
            workspace_intent_service.get_workspace_access(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
            )


def test_attach_access_running_with_empty_container_id(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_running_with_runtime(session, owner_user_id, container_id="   ")
    with Session(workspace_unit_engine) as session:
        with pytest.raises(WorkspaceInvalidStateError, match="not ready for access"):
            workspace_intent_service.get_workspace_access(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
            )


def test_attach_workspace_not_found(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        with pytest.raises(WorkspaceNotFoundError):
            workspace_intent_service.request_attach_workspace(
                session,
                workspace_id=999_999,
                owner_user_id=owner_user_id,
                requested_by_user_id=owner_user_id,
            )


def test_access_workspace_not_found(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        with pytest.raises(WorkspaceNotFoundError):
            workspace_intent_service.get_workspace_access(
                session,
                workspace_id=999_999,
                owner_user_id=owner_user_id,
            )


def test_attach_access_wrong_owner(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        other = UserAuth(
            username="other_owner",
            email="other@example.com",
            password_hash="h",
        )
        session.add(other)
        session.commit()
        session.refresh(other)
        other_id = other.user_auth_id
        assert other_id is not None

    with Session(workspace_unit_engine) as session:
        wid = _seed_running_with_runtime(session, owner_user_id)

    with Session(workspace_unit_engine) as session:
        with pytest.raises(WorkspaceNotFoundError):
            workspace_intent_service.request_attach_workspace(
                session,
                workspace_id=wid,
                owner_user_id=other_id,
                requested_by_user_id=other_id,
            )
        with pytest.raises(WorkspaceNotFoundError):
            workspace_intent_service.get_workspace_access(
                session,
                workspace_id=wid,
                owner_user_id=other_id,
            )


def test_access_includes_health_issue_when_not_healthy(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_running_with_runtime(
            session,
            owner_user_id,
            health_status=WorkspaceRuntimeHealthStatus.UNKNOWN.value,
        )
    with Session(workspace_unit_engine) as session:
        out = workspace_intent_service.get_workspace_access(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
        )
    assert out.success is True
    assert out.runtime_ready is True
    assert len(out.issues) == 1
    assert out.issues[0].startswith("access:runtime:health:")

    with Session(workspace_unit_engine) as session:
        attach_out = workspace_intent_service.request_attach_workspace(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            requested_by_user_id=owner_user_id,
        )
    assert attach_out.accepted is True
    assert len(attach_out.issues) == 1
