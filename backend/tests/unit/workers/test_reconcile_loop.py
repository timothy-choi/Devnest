"""Tests for the automated reconcile loop (Task 1) and reconcile lease (Task 2)."""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.services.workspace_service.models import Workspace, WorkspaceConfig, WorkspaceJob
from app.services.workspace_service.models.enums import WorkspaceJobStatus, WorkspaceJobType, WorkspaceStatus
from app.services.workspace_service.errors import WorkspaceBusyError
from app.services.workspace_service.services.workspace_intent_service import (
    enqueue_reconcile_runtime_job,
)


# ── Reconcile Lease / Lock Tests (Task 2) ────────────────────────────────────


def _seed_workspace(session: Session, owner_user_id: int, status: str) -> Workspace:
    ws = Workspace(
        owner_user_id=owner_user_id,
        name="test-ws",
        status=status,
    )
    session.add(ws)
    session.flush()
    assert ws.workspace_id is not None
    session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}))
    session.flush()
    session.refresh(ws)
    return ws


def _seed_reconcile_job(
    session: Session,
    workspace_id: int,
    owner_user_id: int,
    job_status: str,
    started_at: datetime | None = None,
) -> WorkspaceJob:
    job = WorkspaceJob(
        workspace_id=workspace_id,
        job_type=WorkspaceJobType.RECONCILE_RUNTIME.value,
        status=job_status,
        requested_by_user_id=owner_user_id,
        requested_config_version=1,
        attempt=1,
        started_at=started_at,
    )
    session.add(job)
    session.flush()
    return job


class TestReconcileLease:
    """DB-level reconcile lease prevents duplicate RECONCILE_RUNTIME jobs."""

    def test_enqueue_succeeds_when_no_existing_reconcile_job(
        self, workspace_job_worker_engine: Engine, owner_user_id: int
    ) -> None:
        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id, WorkspaceStatus.RUNNING.value)
            session.commit()
            ws_id = ws.workspace_id

        with Session(workspace_job_worker_engine) as session:
            result = enqueue_reconcile_runtime_job(session, workspace_id=ws_id)
        assert result.accepted is True

    def test_enqueue_raises_when_queued_reconcile_exists(
        self, workspace_job_worker_engine: Engine, owner_user_id: int
    ) -> None:
        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id, WorkspaceStatus.RUNNING.value)
            session.flush()
            _seed_reconcile_job(session, ws.workspace_id, owner_user_id, WorkspaceJobStatus.QUEUED.value)
            session.commit()
            ws_id = ws.workspace_id

        with Session(workspace_job_worker_engine) as session:
            with pytest.raises(WorkspaceBusyError, match="already queued"):
                enqueue_reconcile_runtime_job(session, workspace_id=ws_id)

    def test_enqueue_raises_when_running_reconcile_within_lease_ttl(
        self, workspace_job_worker_engine: Engine, owner_user_id: int
    ) -> None:
        now = datetime.now(timezone.utc)
        recent_start = now - timedelta(seconds=30)  # Started 30s ago; TTL=120s

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id, WorkspaceStatus.RUNNING.value)
            session.flush()
            _seed_reconcile_job(
                session, ws.workspace_id, owner_user_id,
                WorkspaceJobStatus.RUNNING.value, started_at=recent_start,
            )
            session.commit()
            ws_id = ws.workspace_id

        with patch("app.libs.common.config.get_settings") as mock_cfg:
            mock_cfg.return_value = MagicMock(devnest_reconcile_lease_ttl_seconds=120)
            with Session(workspace_job_worker_engine) as session:
                with pytest.raises(WorkspaceBusyError, match="already running"):
                    enqueue_reconcile_runtime_job(session, workspace_id=ws_id)

    def test_enqueue_succeeds_when_running_reconcile_is_stale(
        self, workspace_job_worker_engine: Engine, owner_user_id: int
    ) -> None:
        """A RUNNING reconcile older than the lease TTL is stale — allow re-enqueue."""
        now = datetime.now(timezone.utc)
        stale_start = now - timedelta(seconds=300)  # Started 300s ago; TTL=120s

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id, WorkspaceStatus.RUNNING.value)
            session.flush()
            _seed_reconcile_job(
                session, ws.workspace_id, owner_user_id,
                WorkspaceJobStatus.RUNNING.value, started_at=stale_start,
            )
            session.commit()
            ws_id = ws.workspace_id

        with patch("app.libs.common.config.get_settings") as mock_cfg:
            mock_cfg.return_value = MagicMock(devnest_reconcile_lease_ttl_seconds=120)
            with Session(workspace_job_worker_engine) as session:
                result = enqueue_reconcile_runtime_job(session, workspace_id=ws_id)
        assert result.accepted is True

    def test_enqueue_fails_for_busy_status(
        self, workspace_job_worker_engine: Engine, owner_user_id: int
    ) -> None:
        """STARTING workspace is 'busy' — reconcile should be rejected."""
        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id, WorkspaceStatus.STARTING.value)
            session.commit()
            ws_id = ws.workspace_id

        with Session(workspace_job_worker_engine) as session:
            with pytest.raises(WorkspaceBusyError):
                enqueue_reconcile_runtime_job(session, workspace_id=ws_id)


class TestReconcileLoopTick:
    """Unit tests for the reconcile loop tick function."""

    def test_tick_enqueues_running_workspaces(
        self, workspace_job_worker_engine: Engine, owner_user_id: int
    ) -> None:
        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id, WorkspaceStatus.RUNNING.value)
            session.commit()
            ws_id = ws.workspace_id

        # Import the sync tick helper from lifespan_reconcile but bypass engine lookup.
        from app.workers.lifespan_reconcile import _run_reconcile_tick_sync

        with patch("app.libs.db.database.get_engine", return_value=workspace_job_worker_engine):
            count = _run_reconcile_tick_sync(
                batch_size=10,
                target_statuses=[WorkspaceStatus.RUNNING.value],
            )

        assert count == 1

        # Verify the job was written.
        from sqlmodel import select as _select  # noqa: PLC0415
        with Session(workspace_job_worker_engine) as session:
            jobs = session.exec(
                _select(WorkspaceJob).where(
                    WorkspaceJob.workspace_id == ws_id,
                    WorkspaceJob.job_type == WorkspaceJobType.RECONCILE_RUNTIME.value,
                )
            ).all()
        assert len(jobs) == 1

    def test_tick_skips_workspace_with_active_lease(
        self, workspace_job_worker_engine: Engine, owner_user_id: int
    ) -> None:
        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id, WorkspaceStatus.RUNNING.value)
            # Pre-existing QUEUED reconcile → lease held.
            _seed_reconcile_job(session, ws.workspace_id, owner_user_id, WorkspaceJobStatus.QUEUED.value)
            session.commit()

        from app.workers.lifespan_reconcile import _run_reconcile_tick_sync

        with patch("app.libs.db.database.get_engine", return_value=workspace_job_worker_engine):
            count = _run_reconcile_tick_sync(
                batch_size=10,
                target_statuses=[WorkspaceStatus.RUNNING.value],
            )

        # Enqueue was skipped (WorkspaceBusyError caught).
        assert count == 0

    def test_tick_respects_batch_size(
        self, workspace_job_worker_engine: Engine, owner_user_id: int
    ) -> None:
        # Create 5 running workspaces with configs, batch_size=2.
        with Session(workspace_job_worker_engine) as session:
            for i in range(5):
                ws = Workspace(owner_user_id=owner_user_id, name=f"ws-{i}", status=WorkspaceStatus.RUNNING.value)
                session.add(ws)
                session.flush()
                assert ws.workspace_id is not None
                session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}))
            session.commit()

        from app.workers.lifespan_reconcile import _run_reconcile_tick_sync

        with patch("app.libs.db.database.get_engine", return_value=workspace_job_worker_engine):
            count = _run_reconcile_tick_sync(
                batch_size=2,
                target_statuses=[WorkspaceStatus.RUNNING.value],
            )

        assert count == 2
