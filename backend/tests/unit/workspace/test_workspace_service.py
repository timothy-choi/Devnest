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
    WorkspaceEvent,
    WorkspaceJob,
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceSecret,
    WorkspaceStatus,
)
from app.services.placement_service.bootstrap import ensure_default_local_execution_node
from app.services.workspace_service.errors import WorkspaceSchedulingCapacityError
from app.services.workspace_service.services.workspace_secret_service import resolve_workspace_runtime_secret_env
from app.services.workspace_service.services import workspace_intent_service
from app.services.workspace_service.services.workspace_event_service import WorkspaceStreamEventType


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


def test_create_workspace_rejects_when_execution_node_at_max_slots(
    workspace_unit_engine: Engine,
    owner_user_id: int,
) -> None:
    from datetime import datetime, timezone

    with Session(workspace_unit_engine) as session:
        node = ensure_default_local_execution_node(session)
        node.max_workspaces = 1
        session.add(node)
        session.flush()
        ws = Workspace(
            name="occupies-slot",
            owner_user_id=owner_user_id,
            status=WorkspaceStatus.CREATING.value,
            is_private=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            execution_node_id=int(node.id),
        )
        session.add(ws)
        session.commit()

    with Session(workspace_unit_engine) as session:
        with pytest.raises(WorkspaceSchedulingCapacityError):
            workspace_intent_service.create_workspace(
                session,
                owner_user_id=owner_user_id,
                body=_sample_create_body(),
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
        assert ws.project_storage_key
        assert ws.execution_node_id is not None

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

        ev = session.exec(
            select(WorkspaceEvent).where(WorkspaceEvent.workspace_id == out.workspace_id),
        ).first()
        assert ev is not None
        assert ev.event_type == WorkspaceStreamEventType.INTENT_QUEUED
        assert ev.status == WorkspaceStatus.CREATING.value
        assert ev.payload_json["job_id"] == out.job_id
        assert ev.payload_json["job_type"] == WorkspaceJobType.CREATE.value


def test_create_workspace_stores_ai_key_encrypted_outside_runtime_env(
    workspace_unit_engine: Engine,
    owner_user_id: int,
) -> None:
    body = CreateWorkspaceRequest(
        name="Secure AI Workspace",
        runtime=WorkspaceRuntimeSpecSchema(
            env={
                "DEVNEST_AI_DEFAULT_PROVIDER": "openai",
                "OPENAI_MODEL": "gpt-4.1-mini",
            }
        ),
        ai_secret={"provider": "openai", "api_key": "sk-test-secret"},
    )

    with Session(workspace_unit_engine) as session:
        out = workspace_intent_service.create_workspace(
            session,
            owner_user_id=owner_user_id,
            body=body,
        )

    with Session(workspace_unit_engine) as session:
        cfg = session.exec(
            select(WorkspaceConfig).where(WorkspaceConfig.workspace_id == out.workspace_id),
        ).first()
        secret = session.exec(
            select(WorkspaceSecret).where(WorkspaceSecret.workspace_id == out.workspace_id),
        ).first()

        assert cfg is not None
        assert cfg.config_json["env"]["DEVNEST_AI_DEFAULT_PROVIDER"] == "openai"
        assert cfg.config_json["env"]["OPENAI_MODEL"] == "gpt-4.1-mini"
        assert "OPENAI_API_KEY" not in cfg.config_json["env"]

        assert secret is not None
        assert secret.secret_name == "OPENAI_API_KEY"
        assert secret.encrypted_value != "sk-test-secret"
        assert resolve_workspace_runtime_secret_env(session, workspace_id=out.workspace_id) == {
            "OPENAI_API_KEY": "sk-test-secret"
        }


def test_create_workspace_assigns_distinct_project_storage_keys_and_public_hosts_when_gateway_enabled(
    workspace_unit_engine: Engine,
    owner_user_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVNEST_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("DEVNEST_BASE_DOMAIN", "app.devnest.local")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()
    try:
        with Session(workspace_unit_engine) as session:
            first = workspace_intent_service.create_workspace(
                session,
                owner_user_id=owner_user_id,
                body=CreateWorkspaceRequest(name="WS-1"),
            )
            second = workspace_intent_service.create_workspace(
                session,
                owner_user_id=owner_user_id,
                body=CreateWorkspaceRequest(name="WS-2"),
            )

        with Session(workspace_unit_engine) as session:
            first_ws = session.get(Workspace, first.workspace_id)
            second_ws = session.get(Workspace, second.workspace_id)
            assert first_ws is not None
            assert second_ws is not None
            assert first_ws.project_storage_key
            assert second_ws.project_storage_key
            assert first_ws.project_storage_key != second_ws.project_storage_key
            assert first_ws.public_host
            assert second_ws.public_host
            assert first_ws.public_host != second_ws.public_host
            assert first_ws.public_host.startswith(f"ws-{first.workspace_id}-")
            assert second_ws.public_host.startswith(f"ws-{second.workspace_id}-")
            assert first_ws.public_host.endswith(".app.devnest.local")
            assert second_ws.public_host.endswith(".app.devnest.local")
    finally:
        get_settings.cache_clear()


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
        assert list(session.exec(select(WorkspaceEvent)).all()) == []


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


def test_deleted_workspace_not_listed_and_get_returns_none(
    workspace_unit_engine: Engine,
    owner_user_id: int,
) -> None:
    with Session(workspace_unit_engine) as session:
        out = workspace_intent_service.create_workspace(
            session,
            owner_user_id=owner_user_id,
            body=_sample_create_body(),
        )
        wid = out.workspace_id
        ws = session.get(Workspace, wid)
        assert ws is not None
        ws.status = WorkspaceStatus.DELETED.value
        session.add(ws)
        session.commit()

    with Session(workspace_unit_engine) as session:
        items, total = workspace_intent_service.list_workspaces(session, owner_user_id=owner_user_id)
        assert total == 0
        assert items == []

    with Session(workspace_unit_engine) as session:
        assert (
            workspace_intent_service.get_workspace(
                session,
                workspace_id=wid,
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
