"""Unit tests for workspace intent methods (SQLite, no orchestrator)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlmodel import Session, select

from app.services.auth_service.models import UserAuth
from app.services.workspace_service.api.schemas.workspace_schemas import WorkspaceRuntimeSpecSchema
from app.services.workspace_service.errors import (
    WorkspaceBusyError,
    WorkspaceInvalidStateError,
    WorkspaceNotFoundError,
)
from app.services.workspace_service.models import Workspace, WorkspaceConfig, WorkspaceJob, WorkspaceJobStatus
from app.services.workspace_service.models.enums import WorkspaceJobType, WorkspaceStatus
from app.services.workspace_service.services import workspace_intent_service


def _seed_workspace(
    session: Session,
    owner_id: int,
    *,
    status: str,
    num_configs: int = 1,
    name: str = "Intent WS",
) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name=name,
        description="unit intent",
        owner_user_id=owner_id,
        status=status,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.flush()
    for v in range(1, num_configs + 1):
        session.add(
            WorkspaceConfig(
                workspace_id=ws.workspace_id,
                version=v,
                config_json={"version_marker": v},
            )
        )
    session.commit()
    session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


def _job_count(session: Session, workspace_id: int) -> int:
    rows = session.exec(select(WorkspaceJob).where(WorkspaceJob.workspace_id == workspace_id)).all()
    return len(list(rows))


def test_request_start_happy_path_stopped(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id, status=WorkspaceStatus.STOPPED.value, num_configs=1)
        before = _job_count(session, wid)
        out = workspace_intent_service.request_start_workspace(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            requested_by_user_id=owner_user_id,
        )

    assert out.accepted is True
    assert out.workspace_id == wid
    assert out.status == WorkspaceStatus.STARTING.value
    assert out.job_type == WorkspaceJobType.START.value
    assert out.requested_config_version == 1
    assert out.issues == ()

    with Session(workspace_unit_engine) as session:
        ws = session.get(Workspace, wid)
        assert ws is not None
        assert ws.status == WorkspaceStatus.STARTING.value
        job = session.get(WorkspaceJob, out.job_id)
        assert job is not None
        assert job.job_type == WorkspaceJobType.START.value
        assert job.status == WorkspaceJobStatus.QUEUED.value
        assert job.requested_config_version == 1
        assert _job_count(session, wid) == before + 1


def test_request_start_happy_path_error_status(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id, status=WorkspaceStatus.ERROR.value)
        out = workspace_intent_service.request_start_workspace(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            requested_by_user_id=owner_user_id,
        )
    assert out.status == WorkspaceStatus.STARTING.value
    assert out.job_type == WorkspaceJobType.START.value


def test_request_stop_happy_path(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id, status=WorkspaceStatus.RUNNING.value)
        out = workspace_intent_service.request_stop_workspace(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            requested_by_user_id=owner_user_id,
        )
    assert out.accepted is True
    assert out.status == WorkspaceStatus.STOPPING.value
    assert out.job_type == WorkspaceJobType.STOP.value
    assert out.requested_config_version == 1


def test_request_restart_happy_path_running_and_stopped(workspace_unit_engine, owner_user_id: int) -> None:
    for st in (WorkspaceStatus.RUNNING.value, WorkspaceStatus.STOPPED.value):
        with Session(workspace_unit_engine) as session:
            wid = _seed_workspace(session, owner_user_id, status=st, name=f"R-{st}")
            out = workspace_intent_service.request_restart_workspace(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                requested_by_user_id=owner_user_id,
            )
        assert out.status == WorkspaceStatus.RESTARTING.value
        assert out.job_type == WorkspaceJobType.RESTART.value


def test_request_delete_happy_path_running_stopped_error(workspace_unit_engine, owner_user_id: int) -> None:
    for st in (
        WorkspaceStatus.RUNNING.value,
        WorkspaceStatus.STOPPED.value,
        WorkspaceStatus.ERROR.value,
    ):
        with Session(workspace_unit_engine) as session:
            wid = _seed_workspace(session, owner_user_id, status=st, name=f"D-{st}")
            out = workspace_intent_service.request_delete_workspace(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                requested_by_user_id=owner_user_id,
            )
        assert out.status == WorkspaceStatus.DELETING.value
        assert out.job_type == WorkspaceJobType.DELETE.value


def test_request_update_happy_path_creates_config_v2_and_job(workspace_unit_engine, owner_user_id: int) -> None:
    runtime = WorkspaceRuntimeSpecSchema(image="ghcr.io/new:2", cpu_limit_cores=2.0)
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id, status=WorkspaceStatus.RUNNING.value, num_configs=1)
        out = workspace_intent_service.request_update_workspace(
            session,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            requested_by_user_id=owner_user_id,
            runtime=runtime,
        )

    assert out.accepted is True
    assert out.status == WorkspaceStatus.UPDATING.value
    assert out.job_type == WorkspaceJobType.UPDATE.value
    assert out.requested_config_version == 2

    with Session(workspace_unit_engine) as session:
        ws = session.get(Workspace, wid)
        assert ws is not None
        assert ws.status == WorkspaceStatus.UPDATING.value
        cfg2 = session.exec(
            select(WorkspaceConfig).where(
                WorkspaceConfig.workspace_id == wid,
                WorkspaceConfig.version == 2,
            )
        ).first()
        assert cfg2 is not None
        assert cfg2.config_json == runtime.to_config_dict()
        job = session.get(WorkspaceJob, out.job_id)
        assert job is not None
        assert job.requested_config_version == 2


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
def test_request_start_rejected_when_busy(workspace_unit_engine, owner_user_id: int, busy_status: str) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id, status=busy_status)
        n = _job_count(session, wid)
        with pytest.raises(WorkspaceBusyError):
            workspace_intent_service.request_start_workspace(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                requested_by_user_id=owner_user_id,
            )
        assert _job_count(session, wid) == n


@pytest.mark.parametrize("busy_status", [WorkspaceStatus.STARTING.value, WorkspaceStatus.UPDATING.value])
def test_request_stop_rejected_when_busy(workspace_unit_engine, owner_user_id: int, busy_status: str) -> None:
    """Stop when RUNNING but busy overlay: seed RUNNING then is impossible with single status — use STARTING as busy."""
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id, status=busy_status)
        n = _job_count(session, wid)
        with pytest.raises(WorkspaceBusyError):
            workspace_intent_service.request_stop_workspace(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                requested_by_user_id=owner_user_id,
            )
        assert _job_count(session, wid) == n


def test_all_intents_rejected_when_starting_busy(workspace_unit_engine, owner_user_id: int) -> None:
    """One row in STARTING; each intent raises WorkspaceBusyError and does not add a job."""

    def _run_start(s: Session, wid: int) -> None:
        workspace_intent_service.request_start_workspace(
            s, workspace_id=wid, owner_user_id=owner_user_id, requested_by_user_id=owner_user_id
        )

    def _run_stop(s: Session, wid: int) -> None:
        workspace_intent_service.request_stop_workspace(
            s, workspace_id=wid, owner_user_id=owner_user_id, requested_by_user_id=owner_user_id
        )

    def _run_restart(s: Session, wid: int) -> None:
        workspace_intent_service.request_restart_workspace(
            s, workspace_id=wid, owner_user_id=owner_user_id, requested_by_user_id=owner_user_id
        )

    def _run_delete(s: Session, wid: int) -> None:
        workspace_intent_service.request_delete_workspace(
            s, workspace_id=wid, owner_user_id=owner_user_id, requested_by_user_id=owner_user_id
        )

    def _run_update(s: Session, wid: int) -> None:
        workspace_intent_service.request_update_workspace(
            s,
            workspace_id=wid,
            owner_user_id=owner_user_id,
            requested_by_user_id=owner_user_id,
            runtime=WorkspaceRuntimeSpecSchema(image="x"),
        )

    callers: list[Callable[[Session, int], None]] = [
        _run_start,
        _run_stop,
        _run_restart,
        _run_delete,
        _run_update,
    ]

    for fn in callers:
        with Session(workspace_unit_engine) as session:
            wid = _seed_workspace(session, owner_user_id, status=WorkspaceStatus.STARTING.value)
            n = _job_count(session, wid)
            with pytest.raises(WorkspaceBusyError):
                fn(session, wid)
            assert _job_count(session, wid) == n


def test_invalid_start_when_running(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id, status=WorkspaceStatus.RUNNING.value)
        n = _job_count(session, wid)
        with pytest.raises(WorkspaceInvalidStateError, match="Start is only allowed"):
            workspace_intent_service.request_start_workspace(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                requested_by_user_id=owner_user_id,
            )
        assert _job_count(session, wid) == n


def test_invalid_stop_when_stopped(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id, status=WorkspaceStatus.STOPPED.value)
        n = _job_count(session, wid)
        with pytest.raises(WorkspaceInvalidStateError, match="Stop is only allowed"):
            workspace_intent_service.request_stop_workspace(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                requested_by_user_id=owner_user_id,
            )
        assert _job_count(session, wid) == n


def test_invalid_restart_when_error(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id, status=WorkspaceStatus.ERROR.value)
        n = _job_count(session, wid)
        with pytest.raises(WorkspaceInvalidStateError, match="Restart is only allowed"):
            workspace_intent_service.request_restart_workspace(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                requested_by_user_id=owner_user_id,
            )
        assert _job_count(session, wid) == n


def test_invalid_delete_when_creating_not_busy_semantics_is_busy(workspace_unit_engine, owner_user_id: int) -> None:
    """CREATING is busy, not invalid-state."""
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id, status=WorkspaceStatus.CREATING.value)
        n = _job_count(session, wid)
        with pytest.raises(WorkspaceBusyError):
            workspace_intent_service.request_delete_workspace(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                requested_by_user_id=owner_user_id,
            )
        assert _job_count(session, wid) == n


def test_update_rejected_when_no_config_rows(workspace_unit_engine, owner_user_id: int) -> None:
    now = datetime.now(timezone.utc)
    with Session(workspace_unit_engine) as session:
        ws = Workspace(
            name="no cfg",
            owner_user_id=owner_user_id,
            status=WorkspaceStatus.RUNNING.value,
            is_private=True,
            created_at=now,
            updated_at=now,
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
        wid = ws.workspace_id
        assert wid is not None
        n = _job_count(session, wid)
        with pytest.raises(WorkspaceInvalidStateError, match="no configuration version"):
            workspace_intent_service.request_update_workspace(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                requested_by_user_id=owner_user_id,
                runtime=WorkspaceRuntimeSpecSchema(image="x"),
            )
        assert _job_count(session, wid) == n


def test_intent_rejects_missing_workspace(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        with pytest.raises(WorkspaceNotFoundError):
            workspace_intent_service.request_start_workspace(
                session,
                workspace_id=999_999,
                owner_user_id=owner_user_id,
                requested_by_user_id=owner_user_id,
            )


def test_intent_rejects_wrong_owner(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        other = UserAuth(username="other2", email="other2@example.com", password_hash="h")
        session.add(other)
        session.commit()
        session.refresh(other)
        other_id = other.user_auth_id
        assert other_id is not None
        wid = _seed_workspace(session, owner_user_id, status=WorkspaceStatus.STOPPED.value)

    with Session(workspace_unit_engine) as session:
        n = _job_count(session, wid)
        with pytest.raises(WorkspaceNotFoundError):
            workspace_intent_service.request_start_workspace(
                session,
                workspace_id=wid,
                owner_user_id=other_id,
                requested_by_user_id=other_id,
            )
        assert _job_count(session, wid) == n


def test_start_without_config_version_raises_invalid_state(workspace_unit_engine, owner_user_id: int) -> None:
    now = datetime.now(timezone.utc)
    with Session(workspace_unit_engine) as session:
        ws = Workspace(
            name="no cfg start",
            owner_user_id=owner_user_id,
            status=WorkspaceStatus.STOPPED.value,
            is_private=True,
            created_at=now,
            updated_at=now,
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
        wid = ws.workspace_id
        assert wid is not None
        with pytest.raises(WorkspaceInvalidStateError, match="no configuration version"):
            workspace_intent_service.request_start_workspace(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                requested_by_user_id=owner_user_id,
            )


def test_request_start_commit_failure_rolls_back(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id, status=WorkspaceStatus.STOPPED.value)
        n = _job_count(session, wid)
        with patch.object(session, "commit", side_effect=RuntimeError("commit failed")):
            with pytest.raises(RuntimeError, match="commit failed"):
                workspace_intent_service.request_start_workspace(
                    session,
                    workspace_id=wid,
                    owner_user_id=owner_user_id,
                    requested_by_user_id=owner_user_id,
                )

    with Session(workspace_unit_engine) as session:
        ws = session.get(Workspace, wid)
        assert ws is not None
        assert ws.status == WorkspaceStatus.STOPPED.value
        assert _job_count(session, wid) == n


def test_request_update_commit_failure_rolls_back(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        wid = _seed_workspace(session, owner_user_id, status=WorkspaceStatus.RUNNING.value, num_configs=1)
        n = _job_count(session, wid)
        with patch.object(session, "commit", side_effect=RuntimeError("commit failed")):
            with pytest.raises(RuntimeError, match="commit failed"):
                workspace_intent_service.request_update_workspace(
                    session,
                    workspace_id=wid,
                    owner_user_id=owner_user_id,
                    requested_by_user_id=owner_user_id,
                    runtime=WorkspaceRuntimeSpecSchema(image="nope"),
                )

    with Session(workspace_unit_engine) as session:
        ws = session.get(Workspace, wid)
        assert ws is not None
        assert ws.status == WorkspaceStatus.RUNNING.value
        assert _job_count(session, wid) == n
        cfg2 = session.exec(
            select(WorkspaceConfig).where(
                WorkspaceConfig.workspace_id == wid,
                WorkspaceConfig.version == 2,
            )
        ).first()
        assert cfg2 is None
