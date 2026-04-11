"""Integration tests: workspace intent methods on PostgreSQL (worker-isolated DB, truncate per test).

Commit failures during intent persistence are covered with mocks in
``tests/unit/workspace/test_workspace_intent_service.py``; simulating arbitrary DB faults here is
brittle and adds little beyond real rollback tests already exercised there.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

import pytest
from sqlalchemy import func
from sqlmodel import Session, select

from app.services.auth_service.models import UserAuth
from app.services.workspace_service.api.schemas.workspace_schemas import WorkspaceRuntimeSpecSchema
from app.services.workspace_service.errors import (
    WorkspaceBusyError,
    WorkspaceInvalidStateError,
    WorkspaceNotFoundError,
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
        username="ws_int_intent_owner",
        email="ws_int_intent_owner@example.com",
        password_hash="not-used",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.user_auth_id is not None
    return user.user_auth_id


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
        description="integration intent",
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
    stmt = select(func.count()).select_from(WorkspaceJob).where(WorkspaceJob.workspace_id == workspace_id)
    return int(session.exec(stmt).one())


def test_request_start_happy_path_persists_starting_and_job(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace(db_session, owner_id, status=WorkspaceStatus.STOPPED.value)
    before = _job_count(db_session, wid)

    out = workspace_intent_service.request_start_workspace(
        db_session,
        workspace_id=wid,
        owner_user_id=owner_id,
        requested_by_user_id=owner_id,
    )

    assert out.accepted is True
    assert out.workspace_id == wid
    assert out.status == WorkspaceStatus.STARTING.value
    assert out.job_type == WorkspaceJobType.START.value
    assert out.requested_config_version == 1
    assert out.issues == ()

    ws = db_session.get(Workspace, wid)
    assert ws is not None
    assert ws.status == WorkspaceStatus.STARTING.value
    job = db_session.get(WorkspaceJob, out.job_id)
    assert job is not None
    assert job.job_type == WorkspaceJobType.START.value
    assert job.status == WorkspaceJobStatus.QUEUED.value
    assert job.requested_config_version == 1
    assert _job_count(db_session, wid) == before + 1


def test_request_stop_happy_path_persists_stopping_and_job(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace(db_session, owner_id, status=WorkspaceStatus.RUNNING.value)

    out = workspace_intent_service.request_stop_workspace(
        db_session,
        workspace_id=wid,
        owner_user_id=owner_id,
        requested_by_user_id=owner_id,
    )

    assert out.status == WorkspaceStatus.STOPPING.value
    assert out.job_type == WorkspaceJobType.STOP.value
    ws = db_session.get(Workspace, wid)
    assert ws is not None
    assert ws.status == WorkspaceStatus.STOPPING.value


def test_request_restart_happy_path_persists_restarting_and_job(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace(db_session, owner_id, status=WorkspaceStatus.STOPPED.value)

    out = workspace_intent_service.request_restart_workspace(
        db_session,
        workspace_id=wid,
        owner_user_id=owner_id,
        requested_by_user_id=owner_id,
    )

    assert out.status == WorkspaceStatus.RESTARTING.value
    assert out.job_type == WorkspaceJobType.RESTART.value


def test_request_delete_happy_path_persists_deleting_and_job(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace(db_session, owner_id, status=WorkspaceStatus.ERROR.value)

    out = workspace_intent_service.request_delete_workspace(
        db_session,
        workspace_id=wid,
        owner_user_id=owner_id,
        requested_by_user_id=owner_id,
    )

    assert out.status == WorkspaceStatus.DELETING.value
    assert out.job_type == WorkspaceJobType.DELETE.value


def test_request_update_happy_path_persists_updating_config_v2_and_job(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace(db_session, owner_id, status=WorkspaceStatus.RUNNING.value, num_configs=1)
    runtime = WorkspaceRuntimeSpecSchema(image="ghcr.io/int-update:2", cpu_limit_cores=2.0)

    out = workspace_intent_service.request_update_workspace(
        db_session,
        workspace_id=wid,
        owner_user_id=owner_id,
        requested_by_user_id=owner_id,
        runtime=runtime,
    )

    assert out.status == WorkspaceStatus.UPDATING.value
    assert out.job_type == WorkspaceJobType.UPDATE.value
    assert out.requested_config_version == 2

    job = db_session.get(WorkspaceJob, out.job_id)
    assert job is not None
    assert job.requested_config_version == 2
    cfg2 = db_session.exec(
        select(WorkspaceConfig).where(
            WorkspaceConfig.workspace_id == wid,
            WorkspaceConfig.version == 2,
        )
    ).first()
    assert cfg2 is not None
    assert cfg2.config_json == runtime.to_config_dict()


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
def test_request_start_rejected_when_busy_no_new_job(db_session: Session, busy_status: str) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace(db_session, owner_id, status=busy_status)
    n = _job_count(db_session, wid)
    ws_before = db_session.get(Workspace, wid)
    assert ws_before is not None
    st_before = ws_before.status

    with pytest.raises(WorkspaceBusyError):
        workspace_intent_service.request_start_workspace(
            db_session,
            workspace_id=wid,
            owner_user_id=owner_id,
            requested_by_user_id=owner_id,
        )

    assert _job_count(db_session, wid) == n
    ws_after = db_session.get(Workspace, wid)
    assert ws_after is not None
    assert ws_after.status == st_before


def test_each_intent_rejected_when_starting_busy_no_new_job(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace(db_session, owner_id, status=WorkspaceStatus.STARTING.value)
    n = _job_count(db_session, wid)

    def _start(s: Session) -> None:
        workspace_intent_service.request_start_workspace(
            s, workspace_id=wid, owner_user_id=owner_id, requested_by_user_id=owner_id
        )

    def _stop(s: Session) -> None:
        workspace_intent_service.request_stop_workspace(
            s, workspace_id=wid, owner_user_id=owner_id, requested_by_user_id=owner_id
        )

    def _restart(s: Session) -> None:
        workspace_intent_service.request_restart_workspace(
            s, workspace_id=wid, owner_user_id=owner_id, requested_by_user_id=owner_id
        )

    def _delete(s: Session) -> None:
        workspace_intent_service.request_delete_workspace(
            s, workspace_id=wid, owner_user_id=owner_id, requested_by_user_id=owner_id
        )

    def _update(s: Session) -> None:
        workspace_intent_service.request_update_workspace(
            s,
            workspace_id=wid,
            owner_user_id=owner_id,
            requested_by_user_id=owner_id,
            runtime=WorkspaceRuntimeSpecSchema(image="x"),
        )

    callers: list[Callable[[Session], None]] = [_start, _stop, _restart, _delete, _update]
    for fn in callers:
        with pytest.raises(WorkspaceBusyError):
            fn(db_session)
        assert _job_count(db_session, wid) == n


def test_invalid_start_when_running_no_new_job(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace(db_session, owner_id, status=WorkspaceStatus.RUNNING.value)
    n = _job_count(db_session, wid)
    with pytest.raises(WorkspaceInvalidStateError, match="Start is only allowed"):
        workspace_intent_service.request_start_workspace(
            db_session,
            workspace_id=wid,
            owner_user_id=owner_id,
            requested_by_user_id=owner_id,
        )
    assert _job_count(db_session, wid) == n


def test_invalid_stop_when_stopped_no_new_job(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace(db_session, owner_id, status=WorkspaceStatus.STOPPED.value)
    n = _job_count(db_session, wid)
    with pytest.raises(WorkspaceInvalidStateError, match="Stop is only allowed"):
        workspace_intent_service.request_stop_workspace(
            db_session,
            workspace_id=wid,
            owner_user_id=owner_id,
            requested_by_user_id=owner_id,
        )
    assert _job_count(db_session, wid) == n


def test_invalid_restart_when_error_no_new_job(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace(db_session, owner_id, status=WorkspaceStatus.ERROR.value)
    n = _job_count(db_session, wid)
    with pytest.raises(WorkspaceInvalidStateError, match="Restart is only allowed"):
        workspace_intent_service.request_restart_workspace(
            db_session,
            workspace_id=wid,
            owner_user_id=owner_id,
            requested_by_user_id=owner_id,
        )
    assert _job_count(db_session, wid) == n


def test_delete_when_creating_is_busy_not_invalid_state(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace(db_session, owner_id, status=WorkspaceStatus.CREATING.value)
    n = _job_count(db_session, wid)
    with pytest.raises(WorkspaceBusyError):
        workspace_intent_service.request_delete_workspace(
            db_session,
            workspace_id=wid,
            owner_user_id=owner_id,
            requested_by_user_id=owner_id,
        )
    assert _job_count(db_session, wid) == n


def test_update_when_no_config_rows_invalid_state(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="no cfg",
        owner_user_id=owner_id,
        status=WorkspaceStatus.RUNNING.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)
    wid = ws.workspace_id
    assert wid is not None
    n = _job_count(db_session, wid)
    with pytest.raises(WorkspaceInvalidStateError, match="no configuration version"):
        workspace_intent_service.request_update_workspace(
            db_session,
            workspace_id=wid,
            owner_user_id=owner_id,
            requested_by_user_id=owner_id,
            runtime=WorkspaceRuntimeSpecSchema(image="x"),
        )
    assert _job_count(db_session, wid) == n


@pytest.mark.parametrize(
    "call_intent",
    [
        pytest.param(
            lambda s, w, o: workspace_intent_service.request_start_workspace(
                s, workspace_id=w, owner_user_id=o, requested_by_user_id=o
            ),
            id="start",
        ),
        pytest.param(
            lambda s, w, o: workspace_intent_service.request_stop_workspace(
                s, workspace_id=w, owner_user_id=o, requested_by_user_id=o
            ),
            id="stop",
        ),
        pytest.param(
            lambda s, w, o: workspace_intent_service.request_restart_workspace(
                s, workspace_id=w, owner_user_id=o, requested_by_user_id=o
            ),
            id="restart",
        ),
        pytest.param(
            lambda s, w, o: workspace_intent_service.request_delete_workspace(
                s, workspace_id=w, owner_user_id=o, requested_by_user_id=o
            ),
            id="delete",
        ),
        pytest.param(
            lambda s, w, o: workspace_intent_service.request_update_workspace(
                s,
                workspace_id=w,
                owner_user_id=o,
                requested_by_user_id=o,
                runtime=WorkspaceRuntimeSpecSchema(image="nope"),
            ),
            id="update",
        ),
    ],
)
def test_intent_not_found_no_job_created(
    db_session: Session,
    call_intent: Callable[[Session, int, int], object],
) -> None:
    owner_id = _seed_owner(db_session)
    missing_id = 9_999_999
    total_jobs_before = int(db_session.exec(select(func.count()).select_from(WorkspaceJob)).one())

    with pytest.raises(WorkspaceNotFoundError):
        call_intent(db_session, missing_id, owner_id)

    total_jobs_after = int(db_session.exec(select(func.count()).select_from(WorkspaceJob)).one())
    assert total_jobs_after == total_jobs_before


def test_intent_wrong_owner_no_new_job(db_session: Session) -> None:
    owner_id = _seed_owner(db_session)
    other = UserAuth(username="ws_int_other", email="ws_int_other@example.com", password_hash="h")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    other_id = other.user_auth_id
    assert other_id is not None
    wid = _seed_workspace(db_session, owner_id, status=WorkspaceStatus.STOPPED.value)
    n = _job_count(db_session, wid)

    with pytest.raises(WorkspaceNotFoundError):
        workspace_intent_service.request_start_workspace(
            db_session,
            workspace_id=wid,
            owner_user_id=other_id,
            requested_by_user_id=other_id,
        )
    assert _job_count(db_session, wid) == n
