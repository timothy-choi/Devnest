"""Integration tests: workspace_intent_service on PostgreSQL (worker-isolated DB, table truncate per test).

Commit failures during ``create_workspace`` are exercised with mocks in
``tests/unit/workspace/test_workspace_service.py``; forcing a mid-transaction DB error here is
brittle and adds little beyond the real rollback behavior already covered there.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.services.auth_service.models import UserAuth
from app.services.workspace_service.api.schemas import CreateWorkspaceRequest
from app.services.workspace_service.api.schemas.workspace_schemas import (
    PortMappingSchema,
    WorkspaceRuntimeSpecSchema,
)
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceJob,
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceStatus,
)
from app.services.workspace_service.services import workspace_intent_service


def _seed_owner(session: Session) -> int:
    user = UserAuth(
        username="ws_int_svc_owner",
        email="ws_int_svc_owner@example.com",
        password_hash="not-used",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.user_auth_id is not None
    return user.user_auth_id


def _rich_body() -> CreateWorkspaceRequest:
    return CreateWorkspaceRequest(
        name="My Workspace",
        description="integration test workspace",
        is_private=False,
        runtime=WorkspaceRuntimeSpecSchema(
            image="ghcr.io/example/workspace:1.0",
            cpu_limit_cores=2.0,
            memory_limit_mib=4096,
            env={"LOG_LEVEL": "info"},
            ports=[PortMappingSchema(container_port=8080, host_port=19080)],
            topology_id=1001,
            storage={"ephemeral_gib": 8},
        ),
    )


def test_create_workspace_happy_path_persists_workspace_config_and_job(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    body = _rich_body()

    out = workspace_intent_service.create_workspace(
        db_session,
        owner_user_id=owner_id,
        body=body,
    )

    assert out.status == WorkspaceStatus.CREATING.value
    assert out.config_version == 1
    assert isinstance(out.workspace_id, int)
    assert isinstance(out.job_id, int)

    ws = db_session.get(Workspace, out.workspace_id)
    assert ws is not None
    assert ws.name == "My Workspace"
    assert ws.description == "integration test workspace"
    assert ws.owner_user_id == owner_id
    assert ws.status == WorkspaceStatus.CREATING.value
    assert ws.is_private is False

    cfg = db_session.exec(
        select(WorkspaceConfig).where(WorkspaceConfig.workspace_id == out.workspace_id),
    ).first()
    assert cfg is not None
    assert cfg.version == 1
    assert cfg.config_json == body.runtime.to_config_dict()

    job = db_session.get(WorkspaceJob, out.job_id)
    assert job is not None
    assert job.workspace_id == out.workspace_id
    assert job.job_type == WorkspaceJobType.CREATE.value
    assert job.status == WorkspaceJobStatus.QUEUED.value
    assert job.requested_by_user_id == owner_id
    assert job.requested_config_version == 1
    assert job.attempt == 0


def test_list_workspaces_empty_for_owner(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    items, total = workspace_intent_service.list_workspaces(db_session, owner_user_id=owner_id)
    assert items == []
    assert total == 0


def test_list_workspaces_scoped_and_ordered_newest_first(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    workspace_intent_service.create_workspace(
        db_session,
        owner_user_id=owner_id,
        body=CreateWorkspaceRequest(name="Older WS"),
    )
    workspace_intent_service.create_workspace(
        db_session,
        owner_user_id=owner_id,
        body=CreateWorkspaceRequest(name="Newer WS"),
    )

    other = UserAuth(username="ws_other", email="ws_other@example.com", password_hash="h")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    other_id = other.user_auth_id
    assert other_id is not None
    workspace_intent_service.create_workspace(
        db_session,
        owner_user_id=other_id,
        body=CreateWorkspaceRequest(name="Belongs To Other"),
    )

    mine, total_mine = workspace_intent_service.list_workspaces(db_session, owner_user_id=owner_id)
    assert total_mine == 2
    assert [i.name for i in mine] == ["Newer WS", "Older WS"]

    theirs, total_theirs = workspace_intent_service.list_workspaces(db_session, owner_user_id=other_id)
    assert total_theirs == 1
    assert theirs[0].name == "Belongs To Other"


def test_list_workspaces_pagination(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    for i in range(3):
        workspace_intent_service.create_workspace(
            db_session,
            owner_user_id=owner_id,
            body=CreateWorkspaceRequest(name=f"Page-{i}"),
        )
    page1, total = workspace_intent_service.list_workspaces(
        db_session, owner_user_id=owner_id, skip=0, limit=2
    )
    page2, total2 = workspace_intent_service.list_workspaces(
        db_session, owner_user_id=owner_id, skip=2, limit=10
    )
    assert total == total2 == 3
    assert len(page1) == 2
    assert len(page2) == 1


def test_get_workspace_returns_detail_and_latest_config_version(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    out = workspace_intent_service.create_workspace(
        db_session,
        owner_user_id=owner_id,
        body=CreateWorkspaceRequest(name="Detail WS", description="d"),
    )
    db_session.add(
        WorkspaceConfig(
            workspace_id=out.workspace_id,
            version=2,
            config_json={"rolled": True},
        )
    )
    db_session.commit()

    detail = workspace_intent_service.get_workspace(
        db_session,
        workspace_id=out.workspace_id,
        owner_user_id=owner_id,
    )
    assert detail is not None
    assert detail.workspace_id == out.workspace_id
    assert detail.name == "Detail WS"
    assert detail.description == "d"
    assert detail.owner_user_id == owner_id
    assert detail.status == WorkspaceStatus.CREATING.value
    assert detail.latest_config_version == 2


def test_get_workspace_missing_returns_none(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    assert (
        workspace_intent_service.get_workspace(
            db_session,
            workspace_id=88_888_888,
            owner_user_id=owner_id,
        )
        is None
    )


def test_get_workspace_wrong_owner_returns_none(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    intruder = UserAuth(username="intruder", email="intruder@example.com", password_hash="h")
    db_session.add(intruder)
    db_session.commit()
    db_session.refresh(intruder)
    intruder_id = intruder.user_auth_id
    assert intruder_id is not None

    out = workspace_intent_service.create_workspace(
        db_session,
        owner_user_id=owner_id,
        body=CreateWorkspaceRequest(name="Private to owner"),
    )

    assert (
        workspace_intent_service.get_workspace(
            db_session,
            workspace_id=out.workspace_id,
            owner_user_id=intruder_id,
        )
        is None
    )
