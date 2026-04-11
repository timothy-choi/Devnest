"""Integration tests: attach/access service on PostgreSQL (worker-isolated DB, truncate per test)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlmodel import Session, select

from app.services.auth_service.models import UserAuth
from app.services.workspace_service.errors import (
    WorkspaceBusyError,
    WorkspaceInvalidStateError,
    WorkspaceNotFoundError,
)
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceRuntime,
    WorkspaceRuntimeHealthStatus,
    WorkspaceStatus,
)
from app.services.workspace_service.services import workspace_intent_service

ENDPOINT_REF = "node-1:12345"
PUBLIC_HOST = "ws-123.devnest.local"
INTERNAL_EP = "10.128.0.10:8080"
CONTAINER_ID = "ctr-integration-ready"


def _seed_owner(session: Session) -> int:
    user = UserAuth(
        username="ws_int_attach_owner",
        email="ws_int_attach_owner@example.com",
        password_hash="not-used",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.user_auth_id is not None
    return user.user_auth_id


def _seed_ready_workspace_with_runtime(
    session: Session,
    owner_id: int,
    *,
    status: str = WorkspaceStatus.RUNNING.value,
    endpoint_ref: str = ENDPOINT_REF,
    public_host: str = PUBLIC_HOST,
    internal_endpoint: str = INTERNAL_EP,
    container_id: str = CONTAINER_ID,
    health_status: str = WorkspaceRuntimeHealthStatus.HEALTHY.value,
    active_sessions_count: int = 0,
    name: str = "Attach Access Int WS",
) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name=name,
        description="integration attach/access",
        owner_user_id=owner_id,
        status=status,
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
            node_id="node-int-1",
            container_id=container_id,
            container_state="running",
            topology_id=100,
            internal_endpoint=internal_endpoint,
            config_version=1,
            health_status=health_status,
        )
    )
    session.commit()
    session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


def _runtime_row(session: Session, workspace_id: int) -> WorkspaceRuntime | None:
    return session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id)).first()


def test_request_attach_happy_path_persists_session_count_and_matches_runtime(
    db_session: Session,
) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_ready_workspace_with_runtime(db_session, owner_id, active_sessions_count=0)

    out = workspace_intent_service.request_attach_workspace(
        db_session,
        workspace_id=wid,
        owner_user_id=owner_id,
        requested_by_user_id=owner_id,
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

    ws = db_session.get(Workspace, wid)
    assert ws is not None
    assert ws.active_sessions_count == 1
    assert ws.endpoint_ref == ENDPOINT_REF
    assert ws.public_host == PUBLIC_HOST
    rt = _runtime_row(db_session, wid)
    assert rt is not None
    assert rt.internal_endpoint == INTERNAL_EP
    assert (rt.container_id or "").strip() == CONTAINER_ID


def test_get_workspace_access_happy_path_read_only_matches_persisted_rows(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_ready_workspace_with_runtime(db_session, owner_id, active_sessions_count=4)

    out = workspace_intent_service.get_workspace_access(
        db_session,
        workspace_id=wid,
        owner_user_id=owner_id,
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

    ws = db_session.get(Workspace, wid)
    assert ws is not None
    assert ws.active_sessions_count == 4


def test_attach_rejected_when_stopped_even_if_runtime_row_exists(db_session: Session) -> None:
    """Stale runtime row must not yield attach success when control-plane status is not RUNNING."""
    owner_id = _seed_owner(db_session)
    wid = _seed_ready_workspace_with_runtime(db_session, owner_id, status=WorkspaceStatus.STOPPED.value)

    with pytest.raises(WorkspaceInvalidStateError, match="RUNNING"):
        workspace_intent_service.request_attach_workspace(
            db_session,
            workspace_id=wid,
            owner_user_id=owner_id,
            requested_by_user_id=owner_id,
        )


def test_access_rejected_when_stopped_even_if_runtime_row_exists(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_ready_workspace_with_runtime(db_session, owner_id, status=WorkspaceStatus.STOPPED.value)

    with pytest.raises(WorkspaceInvalidStateError, match="RUNNING"):
        workspace_intent_service.get_workspace_access(
            db_session,
            workspace_id=wid,
            owner_user_id=owner_id,
        )


def test_attach_access_rejected_when_running_without_runtime_row(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="RunningNoRuntime",
        owner_user_id=owner_id,
        status=WorkspaceStatus.RUNNING.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(ws)
    db_session.flush()
    db_session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}))
    db_session.commit()
    db_session.refresh(ws)
    wid = ws.workspace_id
    assert wid is not None
    assert _runtime_row(db_session, wid) is None

    with pytest.raises(WorkspaceInvalidStateError, match="not ready for access"):
        workspace_intent_service.request_attach_workspace(
            db_session,
            workspace_id=wid,
            owner_user_id=owner_id,
            requested_by_user_id=owner_id,
        )
    with pytest.raises(WorkspaceInvalidStateError, match="not ready for access"):
        workspace_intent_service.get_workspace_access(
            db_session,
            workspace_id=wid,
            owner_user_id=owner_id,
        )


def test_attach_access_rejected_when_container_id_blank(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_ready_workspace_with_runtime(db_session, owner_id, container_id="  ")

    with pytest.raises(WorkspaceInvalidStateError, match="not ready for access"):
        workspace_intent_service.request_attach_workspace(
            db_session,
            workspace_id=wid,
            owner_user_id=owner_id,
            requested_by_user_id=owner_id,
        )
    with pytest.raises(WorkspaceInvalidStateError, match="not ready for access"):
        workspace_intent_service.get_workspace_access(
            db_session,
            workspace_id=wid,
            owner_user_id=owner_id,
        )


def test_attach_access_busy_status_before_runtime_checks(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_ready_workspace_with_runtime(db_session, owner_id, status=WorkspaceStatus.STARTING.value)

    with pytest.raises(WorkspaceBusyError):
        workspace_intent_service.request_attach_workspace(
            db_session,
            workspace_id=wid,
            owner_user_id=owner_id,
            requested_by_user_id=owner_id,
        )
    with pytest.raises(WorkspaceBusyError):
        workspace_intent_service.get_workspace_access(
            db_session,
            workspace_id=wid,
            owner_user_id=owner_id,
        )


def test_attach_workspace_not_found(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)

    with pytest.raises(WorkspaceNotFoundError):
        workspace_intent_service.request_attach_workspace(
            db_session,
            workspace_id=99_999_999,
            owner_user_id=owner_id,
            requested_by_user_id=owner_id,
        )


def test_access_workspace_not_found(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)

    with pytest.raises(WorkspaceNotFoundError):
        workspace_intent_service.get_workspace_access(
            db_session,
            workspace_id=99_999_999,
            owner_user_id=owner_id,
        )


def test_attach_access_wrong_owner(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    other = UserAuth(
        username="ws_int_attach_other",
        email="ws_int_attach_other@example.com",
        password_hash="x",
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    other_id = other.user_auth_id
    assert other_id is not None

    wid = _seed_ready_workspace_with_runtime(db_session, owner_id)

    with pytest.raises(WorkspaceNotFoundError):
        workspace_intent_service.request_attach_workspace(
            db_session,
            workspace_id=wid,
            owner_user_id=other_id,
            requested_by_user_id=other_id,
        )
    with pytest.raises(WorkspaceNotFoundError):
        workspace_intent_service.get_workspace_access(
            db_session,
            workspace_id=wid,
            owner_user_id=other_id,
        )


def test_access_reflects_non_healthy_runtime_issue(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_ready_workspace_with_runtime(
        db_session,
        owner_id,
        health_status=WorkspaceRuntimeHealthStatus.DEGRADED.value,
    )

    out = workspace_intent_service.get_workspace_access(
        db_session,
        workspace_id=wid,
        owner_user_id=owner_id,
    )
    assert out.success is True
    assert out.runtime_ready is True
    assert len(out.issues) == 1
    assert "access:runtime:health:" in out.issues[0]

    attach_out = workspace_intent_service.request_attach_workspace(
        db_session,
        workspace_id=wid,
        owner_user_id=owner_id,
        requested_by_user_id=owner_id,
    )
    assert attach_out.accepted is True
    assert len(attach_out.issues) == 1
