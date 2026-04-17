"""Unit tests: workspace attach/access service (SQLite, no worker/orchestrator)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func
from sqlmodel import Session, select

from app.services.auth_service.models import UserAuth
from app.services.workspace_service.errors import (
    WorkspaceAccessDeniedError,
    WorkspaceBusyError,
    WorkspaceInvalidStateError,
    WorkspaceNotFoundError,
)
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceJob,
    WorkspaceRuntime,
    WorkspaceSession,
)
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


def test_attach_does_not_create_workspace_job(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_running_with_runtime(session, owner_user_id, active_sessions_count=0)
        n_jobs = int(
            session.exec(select(func.count()).where(WorkspaceJob.workspace_id == wid)).one(),
        )
    assert n_jobs == 0
    with Session(workspace_unit_engine) as session:
        workspace_intent_service.request_attach_workspace(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            requested_by_user_id=owner_user_id,
        )
    with Session(workspace_unit_engine) as session:
        n_after = int(
            session.exec(select(func.count()).where(WorkspaceJob.workspace_id == wid)).one(),
        )
    assert n_after == 0


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
    assert out.workspace_session_id > 0
    assert out.session_token.startswith("dnws_")
    exp = out.session_expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    assert exp > datetime.now(timezone.utc)

    with Session(workspace_unit_engine) as session:
        ws = session.get(Workspace, wid)
        assert ws is not None
        assert ws.active_sessions_count == 1


def test_get_workspace_access_happy_path(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_running_with_runtime(session, owner_user_id, active_sessions_count=0)
    with Session(workspace_unit_engine) as session:
        att = workspace_intent_service.request_attach_workspace(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            requested_by_user_id=owner_user_id,
        )
        tok = att.session_token
    with Session(workspace_unit_engine) as session:
        out = workspace_intent_service.get_workspace_access(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            workspace_session_token=tok,
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
        assert ws.active_sessions_count == 1


def test_get_workspace_access_does_not_increment_sessions(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_running_with_runtime(session, owner_user_id, active_sessions_count=0)
    with Session(workspace_unit_engine) as session:
        att = workspace_intent_service.request_attach_workspace(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            requested_by_user_id=owner_user_id,
        )
        tok = att.session_token
    with Session(workspace_unit_engine) as session:
        workspace_intent_service.get_workspace_access(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            workspace_session_token=tok,
        )
        workspace_intent_service.get_workspace_access(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            workspace_session_token=tok,
        )
    with Session(workspace_unit_engine) as session:
        ws = session.get(Workspace, wid)
        assert ws is not None
        assert ws.active_sessions_count == 1


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
    assert a.session_token != b.session_token


def test_get_workspace_access_requires_token(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_running_with_runtime(session, owner_user_id)
    with Session(workspace_unit_engine) as session:
        with pytest.raises(WorkspaceAccessDeniedError, match="session token required"):
            workspace_intent_service.get_workspace_access(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                workspace_session_token=None,
            )


def test_get_workspace_access_rejects_bad_token(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_running_with_runtime(session, owner_user_id)
    with Session(workspace_unit_engine) as session:
        with pytest.raises(WorkspaceAccessDeniedError, match="Invalid workspace session token"):
            workspace_intent_service.get_workspace_access(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                workspace_session_token="dnws_not_a_real_token",
            )


def test_get_workspace_access_rejects_expired_session(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_running_with_runtime(session, owner_user_id)
    with Session(workspace_unit_engine) as session:
        att = workspace_intent_service.request_attach_workspace(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            requested_by_user_id=owner_user_id,
        )
        sid = att.workspace_session_id
        tok = att.session_token
    with Session(workspace_unit_engine) as session:
        row = session.get(WorkspaceSession, sid)
        assert row is not None
        row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        session.add(row)
        session.commit()
    with Session(workspace_unit_engine) as session:
        with pytest.raises(WorkspaceAccessDeniedError, match="expired"):
            workspace_intent_service.get_workspace_access(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                workspace_session_token=tok,
            )
    with Session(workspace_unit_engine) as session:
        ws = session.get(Workspace, wid)
        assert ws is not None
        assert ws.active_sessions_count == 0


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
                workspace_session_token=None,
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
                workspace_session_token=None,
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
                workspace_session_token=None,
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
                workspace_session_token=None,
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
                workspace_session_token=None,
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
                workspace_session_token=None,
            )


def test_access_includes_health_issue_when_not_healthy(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_running_with_runtime(
            session,
            owner_user_id,
            health_status=WorkspaceRuntimeHealthStatus.UNKNOWN.value,
        )
    with Session(workspace_unit_engine) as session:
        attach_out = workspace_intent_service.request_attach_workspace(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            requested_by_user_id=owner_user_id,
        )
        tok = attach_out.session_token
    assert attach_out.accepted is True
    assert len(attach_out.issues) == 1

    with Session(workspace_unit_engine) as session:
        out = workspace_intent_service.get_workspace_access(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            workspace_session_token=tok,
        )
    assert out.success is True
    assert out.runtime_ready is True
    assert len(out.issues) == 1
    assert out.issues[0].startswith("access:runtime:health:")


def test_attach_gateway_url_includes_public_port(
    workspace_unit_engine,
    owner_user_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVNEST_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("DEVNEST_BASE_DOMAIN", "app.devnest.local")
    monkeypatch.setenv("DEVNEST_GATEWAY_PUBLIC_PORT", "9081")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()
    try:
        with Session(workspace_unit_engine) as session:
            wid = _seed_running_with_runtime(
                session,
                owner_user_id,
                public_host=None,
                name="gw-port-ws",
            )
            out = workspace_intent_service.request_attach_workspace(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                requested_by_user_id=owner_user_id,
                client_metadata={},
                correlation_id=None,
            )
        assert out.gateway_url == f"http://ws-{wid}.app.devnest.local:9081/"
    finally:
        get_settings.cache_clear()
