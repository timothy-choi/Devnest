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
    }


def classify_reconcile_failure(message: str) -> tuple[FailureStage, bool]:
    """Retryable vs terminal for reconcile paths (conservative: transient infra → retry)."""
    m = (message or "").strip().lower()
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


def queued_job_eligible_where(job_model_type: type, now: datetime):
    """SQL filter: QUEUED and (no backoff or backoff elapsed)."""
    return and_(
        job_model_type.status == WorkspaceJobStatus.QUEUED.value,
        or_(
            job_model_type.next_attempt_after.is_(None),  # type: ignore[union-attr]
            job_model_type.next_attempt_after <= now,
        ),
    )
