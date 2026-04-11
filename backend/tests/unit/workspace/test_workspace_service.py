"""Unit tests for workspace_intent_service (SQLite persistence, no HTTP)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import Engine
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


def _sample_create_body() -> CreateWorkspaceRequest:
    return CreateWorkspaceRequest(
        name="My Workspace",
        description="test workspace",
        is_private=True,
    )


def _sample_create_body_rich_config() -> CreateWorkspaceRequest:
    return CreateWorkspaceRequest(
        name="My Workspace",
        description="test workspace",
        is_private=False,
        runtime=WorkspaceRuntimeSpecSchema(
            image="ghcr.io/example/workspace:1.0",
            cpu_limit_cores=2.0,
            memory_limit_mib=4096,
            env={"FOO": "bar"},
            ports=[PortMappingSchema(container_port=8080, host_port=18080)],
            topology_id=42,
            storage={"size_gib": 10, "class": "fast"},
        ),
    )


def test_create_workspace_request_rejects_blank_name() -> None:
    with pytest.raises(ValidationError):
        CreateWorkspaceRequest(name="   ", description="x")


def test_create_workspace_request_rejects_missing_name() -> None:
    with pytest.raises(ValidationError):
        CreateWorkspaceRequest.model_validate({"description": "only"})


def test_create_workspace_request_rejects_non_positive_cpu() -> None:
    with pytest.raises(ValidationError):
        CreateWorkspaceRequest(
            name="A",
            runtime={"cpu_limit_cores": 0},
        )


def test_create_workspace_happy_path_persists_rows_and_result_shape(
    workspace_unit_engine: Engine,
    owner_user_id: int,
) -> None:
    body = _sample_create_body_rich_config()
    with Session(workspace_unit_engine) as session:
        out = workspace_intent_service.create_workspace(
            session,
            owner_user_id=owner_user_id,
            body=body,
        )

    assert out.status == WorkspaceStatus.CREATING.value
    assert out.config_version == 1
    assert isinstance(out.workspace_id, int)
    assert isinstance(out.job_id, int)

    with Session(workspace_unit_engine) as session:
        ws = session.get(Workspace, out.workspace_id)
        assert ws is not None
        assert ws.name == "My Workspace"
        assert ws.description == "test workspace"
        assert ws.owner_user_id == owner_user_id
        assert ws.status == WorkspaceStatus.CREATING.value
        assert ws.is_private is False

        cfg = session.exec(
            select(WorkspaceConfig).where(WorkspaceConfig.workspace_id == out.workspace_id),
        ).first()
        assert cfg is not None
        assert cfg.version == 1
        assert cfg.config_json == body.runtime.to_config_dict()
        assert cfg.config_json["image"] == "ghcr.io/example/workspace:1.0"
        assert cfg.config_json["cpu_limit_cores"] == 2.0
        assert cfg.config_json["memory_limit_mib"] == 4096
        assert cfg.config_json["env"] == {"FOO": "bar"}
        assert cfg.config_json["ports"] == [{"container_port": 8080, "host_port": 18080}]
        assert cfg.config_json["topology_id"] == 42
        assert cfg.config_json["storage"] == {"size_gib": 10, "class": "fast"}

        job = session.get(WorkspaceJob, out.job_id)
        assert job is not None
        assert job.workspace_id == out.workspace_id
        assert job.job_type == WorkspaceJobType.CREATE.value
        assert job.status == WorkspaceJobStatus.QUEUED.value
        assert job.requested_by_user_id == owner_user_id
        assert job.requested_config_version == 1
        assert job.attempt == 0


def test_create_workspace_commit_failure_rolls_back_no_rows(
    workspace_unit_engine: Engine,
    owner_user_id: int,
) -> None:
    body = _sample_create_body()
    with Session(workspace_unit_engine) as session:
        with patch.object(session, "commit", side_effect=RuntimeError("commit failed")):
            with pytest.raises(RuntimeError, match="commit failed"):
                workspace_intent_service.create_workspace(
                    session,
                    owner_user_id=owner_user_id,
                    body=body,
                )

    with Session(workspace_unit_engine) as session:
        assert list(session.exec(select(Workspace)).all()) == []
        assert list(session.exec(select(WorkspaceConfig)).all()) == []
        assert list(session.exec(select(WorkspaceJob)).all()) == []


def test_list_workspaces_empty(workspace_unit_engine: Engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        items, total = workspace_intent_service.list_workspaces(session, owner_user_id=owner_user_id)
    assert items == []
    assert total == 0


def test_list_workspaces_scoped_to_owner(workspace_unit_engine: Engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        other = UserAuth(username="other", email="other@example.com", password_hash="h")
        session.add(other)
        session.commit()
        session.refresh(other)
        other_id = other.user_auth_id
        assert other_id is not None

    with Session(workspace_unit_engine) as session:
        workspace_intent_service.create_workspace(
            session,
            owner_user_id=owner_user_id,
            body=_sample_create_body(),
        )
        workspace_intent_service.create_workspace(
            session,
            owner_user_id=other_id,
            body=CreateWorkspaceRequest(name="Other WS"),
        )

    with Session(workspace_unit_engine) as session:
        mine, total_mine = workspace_intent_service.list_workspaces(session, owner_user_id=owner_user_id)
        theirs, total_theirs = workspace_intent_service.list_workspaces(session, owner_user_id=other_id)

    assert total_mine == 1
    assert len(mine) == 1
    assert mine[0].name == "My Workspace"

    assert total_theirs == 1
    assert theirs[0].name == "Other WS"


def test_list_workspaces_pagination(workspace_unit_engine: Engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        for i in range(3):
            workspace_intent_service.create_workspace(
                session,
                owner_user_id=owner_user_id,
                body=CreateWorkspaceRequest(name=f"WS-{i}"),
            )

    with Session(workspace_unit_engine) as session:
        page1, total = workspace_intent_service.list_workspaces(
            session, owner_user_id=owner_user_id, skip=0, limit=2
        )
        page2, total2 = workspace_intent_service.list_workspaces(
            session, owner_user_id=owner_user_id, skip=2, limit=10
        )

    assert total == total2 == 3
    assert len(page1) == 2
    assert len(page2) == 1


def test_get_workspace_returns_detail_and_latest_config_version(
    workspace_unit_engine: Engine,
    owner_user_id: int,
) -> None:
    with Session(workspace_unit_engine) as session:
        out = workspace_intent_service.create_workspace(
            session,
            owner_user_id=owner_user_id,
            body=_sample_create_body(),
        )
        session.add(
            WorkspaceConfig(
                workspace_id=out.workspace_id,
                version=2,
                config_json={"bump": True},
            )
        )
        session.commit()

    with Session(workspace_unit_engine) as session:
        detail = workspace_intent_service.get_workspace(
            session,
            workspace_id=out.workspace_id,
            owner_user_id=owner_user_id,
        )

    assert detail is not None
    assert detail.workspace_id == out.workspace_id
    assert detail.name == "My Workspace"
    assert detail.owner_user_id == owner_user_id
    assert detail.status == WorkspaceStatus.CREATING.value
    assert detail.latest_config_version == 2


def test_get_workspace_missing_returns_none(workspace_unit_engine: Engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        assert (
            workspace_intent_service.get_workspace(
                session,
                workspace_id=9_999_999,
                owner_user_id=owner_user_id,
            )
            is None
        )


def test_get_workspace_wrong_owner_returns_none(
    workspace_unit_engine: Engine,
    owner_user_id: int,
) -> None:
    with Session(workspace_unit_engine) as session:
        other = UserAuth(username="intruder", email="intruder@example.com", password_hash="h")
        session.add(other)
        session.commit()
        session.refresh(other)
        intruder_id = other.user_auth_id
        assert intruder_id is not None

        out = workspace_intent_service.create_workspace(
            session,
            owner_user_id=owner_user_id,
            body=_sample_create_body(),
        )
        wid = out.workspace_id

    with Session(workspace_unit_engine) as session:
        assert (
            workspace_intent_service.get_workspace(
                session,
                workspace_id=wid,
                owner_user_id=intruder_id,
            )
            is None
        )


def test_port_mapping_invalid_container_port_rejected() -> None:
    with pytest.raises(ValidationError):
        PortMappingSchema(container_port=0, host_port=8080)
