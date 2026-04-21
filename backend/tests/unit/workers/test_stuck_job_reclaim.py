"""Tests for stuck-job reclaim logic (Task 3: worker lifecycle hardening)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.services.workspace_service.models import Workspace, WorkspaceJob, WorkspaceRuntime
from app.services.workspace_service.models.enums import (
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceStatus,
)
from app.workers.workspace_job_worker.worker import reclaim_stuck_running_jobs


def _make_settings(stuck_timeout: int = 300, max_attempts: int = 2) -> MagicMock:
    s = MagicMock()
    s.workspace_job_stuck_timeout_seconds = stuck_timeout
    s.workspace_job_max_attempts = max_attempts
    s.workspace_job_retry_backoff_seconds = 0
    return s


def _seed(session: Session, owner_user_id: int, status: str = WorkspaceStatus.RUNNING.value) -> Workspace:
    ws = Workspace(owner_user_id=owner_user_id, name="test-ws", status=status)
    session.add(ws)
    session.flush()
    return ws


def _seed_running_job(
    session: Session,
    ws: Workspace,
    owner_user_id: int,
    started_ago_seconds: int,
    job_type: str = WorkspaceJobType.START.value,
    attempt: int = 1,
    max_attempts: int = 2,
) -> WorkspaceJob:
    started_at = datetime.now(timezone.utc) - timedelta(seconds=started_ago_seconds)
    job = WorkspaceJob(
        workspace_id=ws.workspace_id,
        job_type=job_type,
        status=WorkspaceJobStatus.RUNNING.value,
        requested_by_user_id=owner_user_id,
        requested_config_version=1,
        attempt=attempt,
        max_attempts=max_attempts,
        started_at=started_at,
    )
    session.add(job)
    session.flush()
    return job


class TestReclaimStuckJobs:

    def test_no_stuck_jobs_returns_zero(
        self, workspace_job_worker_engine: Engine, owner_user_id: int
    ) -> None:
        with patch("app.workers.workspace_job_worker.worker.get_settings", return_value=_make_settings()):
            count = reclaim_stuck_running_jobs(workspace_job_worker_engine)
        assert count == 0

    def test_disabled_when_timeout_is_zero(
        self, workspace_job_worker_engine: Engine, owner_user_id: int
    ) -> None:
        with Session(workspace_job_worker_engine) as session:
            ws = _seed(session, owner_user_id)
            _seed_running_job(session, ws, owner_user_id, started_ago_seconds=9999)
            session.commit()

        with patch("app.workers.workspace_job_worker.worker.get_settings", return_value=_make_settings(stuck_timeout=0)):
            count = reclaim_stuck_running_jobs(workspace_job_worker_engine)
        assert count == 0

    def test_fresh_running_job_not_reclaimed(
        self, workspace_job_worker_engine: Engine, owner_user_id: int
    ) -> None:
        with Session(workspace_job_worker_engine) as session:
            ws = _seed(session, owner_user_id)
            _seed_running_job(session, ws, owner_user_id, started_ago_seconds=30)
            session.commit()

        with patch("app.workers.workspace_job_worker.worker.get_settings", return_value=_make_settings(stuck_timeout=300)):
            count = reclaim_stuck_running_jobs(workspace_job_worker_engine)
        assert count == 0

    def test_stuck_job_with_retry_remaining_becomes_queued(
        self, workspace_job_worker_engine: Engine, owner_user_id: int
    ) -> None:
        with Session(workspace_job_worker_engine) as session:
            ws = _seed(session, owner_user_id)
            job = _seed_running_job(
                session, ws, owner_user_id,
                started_ago_seconds=600,  # 10 min > 300s timeout
                attempt=1, max_attempts=2,
            )
            session.commit()
            job_id = job.workspace_job_id

        with patch("app.workers.workspace_job_worker.worker.get_settings", return_value=_make_settings()):
            count = reclaim_stuck_running_jobs(workspace_job_worker_engine)
        assert count == 1

        with Session(workspace_job_worker_engine) as session:
            j = session.get(WorkspaceJob, job_id)
            assert j is not None
            # Should be re-queued for retry.
            assert j.status == WorkspaceJobStatus.QUEUED.value

    def test_stuck_job_retry_exhausted_becomes_failed_and_workspace_errors(
        self, workspace_job_worker_engine: Engine, owner_user_id: int
    ) -> None:
        with Session(workspace_job_worker_engine) as session:
            ws = _seed(session, owner_user_id)
            job = _seed_running_job(
                session, ws, owner_user_id,
                started_ago_seconds=600,
                attempt=2, max_attempts=2,  # attempt == max_attempts → exhausted
                job_type=WorkspaceJobType.START.value,
            )
            session.add(
                WorkspaceRuntime(
                    workspace_id=ws.workspace_id,
                    node_id="node-stuck",
                    reserved_cpu=1.0,
                    reserved_memory_mb=512,
                    reserved_disk_mb=4096,
                )
            )
            session.commit()
            job_id = job.workspace_job_id
            ws_id = ws.workspace_id

        with patch("app.workers.workspace_job_worker.worker.get_settings", return_value=_make_settings(max_attempts=2)):
            count = reclaim_stuck_running_jobs(workspace_job_worker_engine)
        assert count == 1

        with Session(workspace_job_worker_engine) as session:
            j = session.get(WorkspaceJob, job_id)
            assert j is not None
            assert j.status == WorkspaceJobStatus.FAILED.value

            w = session.get(Workspace, ws_id)
            assert w is not None
            # Lifecycle job exhausted → workspace moves to ERROR.
            assert w.status == WorkspaceStatus.ERROR.value
            rt = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == ws_id)).first()
            assert rt is not None
            assert rt.reserved_cpu == 0.0
            assert rt.reserved_memory_mb == 0
            assert rt.reserved_disk_mb == 0

    def test_stuck_reconcile_job_retry_exhausted_does_not_error_workspace(
        self, workspace_job_worker_engine: Engine, owner_user_id: int
    ) -> None:
        """A terminal RECONCILE_RUNTIME job should NOT move workspace to ERROR."""
        with Session(workspace_job_worker_engine) as session:
            ws = _seed(session, owner_user_id, WorkspaceStatus.RUNNING.value)
            job = _seed_running_job(
                session, ws, owner_user_id,
                started_ago_seconds=600,
                attempt=2, max_attempts=2,
                job_type=WorkspaceJobType.RECONCILE_RUNTIME.value,
            )
            session.commit()
            job_id = job.workspace_job_id
            ws_id = ws.workspace_id

        with patch("app.workers.workspace_job_worker.worker.get_settings", return_value=_make_settings(max_attempts=2)):
            count = reclaim_stuck_running_jobs(workspace_job_worker_engine)
        assert count == 1

        with Session(workspace_job_worker_engine) as session:
            j = session.get(WorkspaceJob, job_id)
            assert j is not None
            assert j.status == WorkspaceJobStatus.FAILED.value

            w = session.get(Workspace, ws_id)
            assert w is not None
            # Reconcile job should NOT change workspace to ERROR.
            assert w.status == WorkspaceStatus.RUNNING.value
