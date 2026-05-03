"""Failure classification and bounded retry scheduling for workspace jobs (worker-owned, V1)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import and_, or_
from sqlmodel import Session

from app.libs.common.config import get_settings
from app.services.placement_service.errors import NoSchedulableNodeError
from app.services.workspace_service.models.enums import FailureStage, WorkspaceJobStatus, WorkspaceJobType

if TYPE_CHECKING:
    from app.services.workspace_service.models import WorkspaceJob

logger = logging.getLogger(__name__)

# User-facing / operator-facing text for capacity wait (API + worker retry); not a terminal error.
WORKSPACE_CAPACITY_PENDING_LAST_ERROR = "Waiting for execution capacity"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def classify_placement_error(exc: BaseException) -> tuple[FailureStage, bool]:
    if isinstance(exc, NoSchedulableNodeError):
        return FailureStage.CAPACITY, True
    return FailureStage.PLACEMENT, True


def orchestrator_binding_retryable() -> tuple[FailureStage, bool]:
    return FailureStage.NETWORK, True


def lifecycle_result_failure_retryable(job_type: str | None) -> bool:
    """Whether orchestrator *result* failure (success=False) may be retried for this job type."""
    if not job_type:
        return False
    return job_type in {
        WorkspaceJobType.CREATE.value,
        WorkspaceJobType.START.value,
        WorkspaceJobType.RESTART.value,
        WorkspaceJobType.UPDATE.value,
        WorkspaceJobType.SNAPSHOT_CREATE.value,
        WorkspaceJobType.SNAPSHOT_RESTORE.value,
    }


def orchestrator_exception_retryable(job_type: str | None) -> bool:
    if not job_type:
        return False
    return job_type in {
        WorkspaceJobType.CREATE.value,
        WorkspaceJobType.START.value,
        WorkspaceJobType.RESTART.value,
        WorkspaceJobType.UPDATE.value,
        WorkspaceJobType.RECONCILE_RUNTIME.value,
        WorkspaceJobType.SNAPSHOT_CREATE.value,
        WorkspaceJobType.SNAPSHOT_RESTORE.value,
    }


def classify_reconcile_failure(message: str) -> tuple[FailureStage, bool]:
    """Retryable vs terminal for reconcile paths (conservative: transient infra → retry)."""
    m = (message or "").strip().lower()
    if "reconcile:advisory_lock_contended" in m:
        return FailureStage.PLACEMENT, True
    if "reconcile:workspace_busy" in m or "reconcile:unsupported_workspace_status" in m:
        return FailureStage.UNKNOWN, False
    if "gateway_" in m or "gateway:" in m:
        return FailureStage.PROXY, True
    if "health_check_failed" in m or "runtime_not_healthy" in m:
        return FailureStage.CONTAINER, True
    if "stop_failed" in m:
        return FailureStage.CONTAINER, True
    return FailureStage.UNKNOWN, True


def effective_max_attempts(job: WorkspaceJob) -> int:
    configured = int(job.max_attempts or 0)
    if configured >= 1:
        return configured
    return max(1, int(get_settings().workspace_job_max_attempts))


def try_schedule_workspace_job_retry(
    session: Session,
    job: WorkspaceJob,
    *,
    message: str,
    stage: FailureStage,
    failure_code: str | None,
    truncate_message: Callable[[str | None, int], str | None],
    now: datetime | None = None,
) -> bool:
    """
    If attempts remain, move job back to ``QUEUED`` with ``next_attempt_after`` backoff.

    Returns True when scheduled (caller must not apply terminal workspace mutation).
    """
    max_a = effective_max_attempts(job)
    current = int(job.attempt or 0)
    if current >= max_a:
        return False
    backoff = max(0, int(get_settings().workspace_job_retry_backoff_seconds))
    ts = now if now is not None else utc_now()
    job.status = WorkspaceJobStatus.QUEUED.value
    job.finished_at = None
    job.started_at = None
    job.error_msg = truncate_message(message, 8192)
    job.failure_stage = stage.value
    job.failure_code = (failure_code or stage.value)[:64]
    job.next_attempt_after = ts + timedelta(seconds=backoff)
    session.add(job)
    logger.info(
        "workspace_job_retry_scheduled",
        extra={
            "workspace_job_id": job.workspace_job_id,
            "workspace_id": job.workspace_id,
            "attempt": current,
            "max_attempts": max_a,
            "failure_stage": stage.value,
            "next_attempt_after": job.next_attempt_after.isoformat() if job.next_attempt_after else None,
        },
    )
    return True


def _as_aware_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def capacity_retry_timeout_seconds() -> int:
    raw = getattr(get_settings(), "workspace_capacity_retry_timeout_seconds", 600)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 600


def capacity_retry_backoff_seconds() -> int:
    raw = getattr(get_settings(), "workspace_capacity_retry_backoff_seconds", 20)
    try:
        return max(0, min(int(raw), 30))
    except (TypeError, ValueError):
        return 20


def capacity_wait_timed_out(job: WorkspaceJob, *, now: datetime | None = None) -> bool:
    ts = now if now is not None else utc_now()
    return (_as_aware_utc(ts) - _as_aware_utc(job.created_at)).total_seconds() >= capacity_retry_timeout_seconds()


def try_schedule_capacity_retry(
    session: Session,
    job: WorkspaceJob,
    *,
    message: str,
    truncate_message: Callable[[str | None, int], str | None],
    now: datetime | None = None,
) -> bool:
    """
    Keep capacity placement failures queued until the capacity wait timeout expires.

    This intentionally does not use ``WorkspaceJob.max_attempts`` because capacity retries are
    autoscaler wait-loop attempts, not repeated runtime/container bring-up attempts.
    """
    ts = now if now is not None else utc_now()
    if capacity_wait_timed_out(job, now=ts):
        return False
    job.status = WorkspaceJobStatus.QUEUED.value
    job.finished_at = None
    job.started_at = None
    job.error_msg = truncate_message(message, 8192)
    job.failure_stage = FailureStage.CAPACITY.value
    job.failure_code = "no_schedulable_node"
    job.next_attempt_after = ts + timedelta(seconds=capacity_retry_backoff_seconds())
    session.add(job)
    logger.info(
        "workspace_capacity_retry_scheduled",
        extra={
            "workspace_job_id": job.workspace_job_id,
            "workspace_id": job.workspace_id,
            "attempt": int(job.attempt or 0),
            "capacity_retry_timeout_seconds": capacity_retry_timeout_seconds(),
            "next_attempt_after": job.next_attempt_after.isoformat() if job.next_attempt_after else None,
        },
    )
    return True


def try_schedule_node_readiness_retry(
    session: Session,
    job: WorkspaceJob,
    *,
    message: str,
    truncate_message: Callable[[str | None, int], str | None],
    now: datetime | None = None,
) -> bool:
    """Keep placement/bring-up jobs queued while a selected execution node finishes bootstrapping."""
    ts = now if now is not None else utc_now()
    if capacity_wait_timed_out(job, now=ts):
        return False
    job.status = WorkspaceJobStatus.QUEUED.value
    job.finished_at = None
    job.started_at = None
    job.error_msg = truncate_message(message, 8192)
    job.failure_stage = FailureStage.CAPACITY.value
    job.failure_code = "node_readiness"
    job.next_attempt_after = ts + timedelta(seconds=capacity_retry_backoff_seconds())
    session.add(job)
    logger.info(
        "workspace.retry.scheduled",
        extra={
            "workspace_job_id": job.workspace_job_id,
            "workspace_id": job.workspace_id,
            "reason": "node_readiness",
            "attempt": int(job.attempt or 0),
            "next_attempt_after": job.next_attempt_after.isoformat() if job.next_attempt_after else None,
        },
    )
    return True


def queued_job_eligible_where(job_model_type: type, now: datetime):
    """SQL filter: QUEUED and (no backoff or backoff elapsed)."""
    return and_(
        job_model_type.status == WorkspaceJobStatus.QUEUED.value,
        or_(
            job_model_type.next_attempt_after.is_(None),  # type: ignore[union-attr]
            job_model_type.next_attempt_after <= now,
        ),
    )
