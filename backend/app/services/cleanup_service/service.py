"""Persist and retry cleanup until orchestrator + topology reach a safe state."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select

from app.libs.observability import metrics as devnest_metrics
from app.services.orchestrator_service.app_factory import build_default_orchestrator_for_session
from app.services.orchestrator_service.interfaces import OrchestratorService
from app.services.placement_service.runtime_policy import runtime_placement_row_complete
from app.services.workspace_service.models import Workspace, WorkspaceCleanupTask, WorkspaceRuntime
from app.services.workspace_service.models.enums import WorkspaceCleanupTaskStatus, WorkspaceRuntimeHealthStatus

logger = logging.getLogger(__name__)

CLEANUP_SCOPE_BRINGUP_ROLLBACK = "bringup_rollback"
CLEANUP_SCOPE_STOP_INCOMPLETE = "stop_incomplete"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_durable_cleanup_task(
    session: Session,
    *,
    workspace_id: int,
    scope: str,
    detail: list[str] | None = None,
) -> None:
    """Insert or refresh a PENDING cleanup task (idempotent per workspace+scope)."""
    detail_str = json.dumps({"issues": detail}, separators=(",", ":")) if detail else None
    row = session.exec(
        select(WorkspaceCleanupTask).where(
            WorkspaceCleanupTask.workspace_id == workspace_id,
            WorkspaceCleanupTask.scope == scope,
        ),
    ).first()
    ts = _now()
    if row is None:
        session.add(
            WorkspaceCleanupTask(
                workspace_id=workspace_id,
                scope=scope,
                detail=detail_str,
                status=WorkspaceCleanupTaskStatus.PENDING.value,
                attempts=0,
                created_at=ts,
                updated_at=ts,
            ),
        )
        devnest_metrics.record_cleanup_task_enqueued(scope=scope)
        return
    if row.status != WorkspaceCleanupTaskStatus.SUCCEEDED.value:
        row.detail = detail_str or row.detail
        row.status = WorkspaceCleanupTaskStatus.PENDING.value
        row.updated_at = ts
        session.add(row)


def process_durable_cleanup_tasks_for_workspace(
    session: Session,
    orchestrator: OrchestratorService,
    ws: Workspace,
    *,
    correlation_id: str | None = None,
) -> int:
    """
    Run pending cleanup task(s) for this workspace: stop + detach + IP release (idempotent).

    Returns the number of tasks moved to SUCCEEDED in this call.
    """
    wid = ws.workspace_id
    assert wid is not None
    tasks = session.exec(
        select(WorkspaceCleanupTask).where(
            WorkspaceCleanupTask.workspace_id == wid,
            WorkspaceCleanupTask.status == WorkspaceCleanupTaskStatus.PENDING.value,
        ),
    ).all()
    if not tasks:
        return 0

    rt = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
    if not runtime_placement_row_complete(rt):
        logger.info(
            "cleanup_task_deferred_incomplete_runtime",
            extra={"workspace_id": wid, "correlation_id": correlation_id},
        )
        return 0

    cid = ((rt.container_id if rt else None) or "").strip() or None
    succeeded = 0
    for task in tasks:
        ts = _now()
        task.attempts = int(task.attempts or 0) + 1
        task.updated_at = ts
        session.add(task)
        session.flush()

        try:
            stop_res = orchestrator.stop_workspace_runtime(
                workspace_id=str(wid),
                container_id=cid,
                release_ip_lease=True,
            )
        except Exception as e:
            task.detail = json.dumps(
                {"error": str(e), "attempt": task.attempts},
                separators=(",", ":"),
            )[:8192]
            task.updated_at = _now()
            session.add(task)
            logger.warning(
                "cleanup_task_stop_exception",
                extra={"workspace_id": wid, "scope": task.scope, "error": str(e)},
                exc_info=True,
            )
            devnest_metrics.record_cleanup_task_attempt(scope=task.scope, result="error")
            continue

        if stop_res.success:
            task.status = WorkspaceCleanupTaskStatus.SUCCEEDED.value
            task.updated_at = _now()
            session.add(task)
            if rt is not None and rt.health_status == WorkspaceRuntimeHealthStatus.CLEANUP_REQUIRED.value:
                rt.health_status = WorkspaceRuntimeHealthStatus.UNKNOWN.value
                rt.updated_at = _now()
                session.add(rt)
            succeeded += 1
            devnest_metrics.record_cleanup_task_attempt(scope=task.scope, result="succeeded")
            logger.info(
                "cleanup_task_succeeded",
                extra={
                    "workspace_id": wid,
                    "scope": task.scope,
                    "attempts": task.attempts,
                    "correlation_id": correlation_id,
                },
            )
        else:
            issues = list(stop_res.issues or [])
            payload: dict[str, Any] = {"issues": issues, "attempt": task.attempts}
            task.detail = json.dumps(payload, separators=(",", ":"))[:8192]
            task.updated_at = _now()
            session.add(task)
            devnest_metrics.record_cleanup_task_attempt(scope=task.scope, result="incomplete")
            logger.warning(
                "cleanup_task_stop_incomplete",
                extra={
                    "workspace_id": wid,
                    "scope": task.scope,
                    "attempts": task.attempts,
                    "issues": issues[:5],
                },
            )

    return succeeded


def _merge_cleanup_deferred_note(
    tasks: list[WorkspaceCleanupTask],
    *,
    reason: str,
    workspace_id: int,
    extra: dict[str, Any] | None = None,
) -> None:
    """Record why cleanup could not run (idempotent: skip if payload already matches)."""
    extra = extra or {}
    for task in tasks:
        payload: dict[str, Any] = {}
        if task.detail:
            try:
                payload = json.loads(task.detail)
            except json.JSONDecodeError:
                payload = {"previous_detail": (task.detail or "")[:512]}
        if payload.get("deferred_reason") == reason and int(payload.get("deferred_workspace_id") or 0) == int(
            workspace_id,
        ):
            continue
        payload["deferred_reason"] = reason
        payload["deferred_workspace_id"] = int(workspace_id)
        payload.update(extra)
        task.detail = json.dumps(payload, separators=(",", ":"))[:8192]
        task.updated_at = _now()


def drain_pending_cleanup_tasks(session: Session, *, limit_workspaces: int = 8) -> int:
    """
    Process pending durable cleanup for up to ``limit_workspaces`` distinct workspaces.

    Runs on ordinary job-worker ticks as well as reconcile so cleanup debt is not tied to a single
    control-plane path. When ``WorkspaceRuntime`` placement is incomplete, tasks keep ``PENDING`` and
    receive a clear ``deferred_reason`` in ``detail`` (no fake success). Idempotent: succeeded tasks
    are skipped by :func:`process_durable_cleanup_tasks_for_workspace`.
    """
    stmt = (
        select(WorkspaceCleanupTask.workspace_id)
        .where(WorkspaceCleanupTask.status == WorkspaceCleanupTaskStatus.PENDING.value)
        .distinct()
        .limit(max(1, int(limit_workspaces)))
    )
    raw_ids = session.exec(stmt).all()
    workspace_ids: list[int] = []
    for row in raw_ids:
        workspace_ids.append(int(row[0]) if isinstance(row, tuple) else int(row))
    total_succeeded = 0
    for wid in workspace_ids:
        ws = session.get(Workspace, wid)
        if ws is None:
            continue
        tasks = list(
            session.exec(
                select(WorkspaceCleanupTask).where(
                    WorkspaceCleanupTask.workspace_id == wid,
                    WorkspaceCleanupTask.status == WorkspaceCleanupTaskStatus.PENDING.value,
                ),
            ).all(),
        )
        if not tasks:
            continue
        rt = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
        if not runtime_placement_row_complete(rt):
            _merge_cleanup_deferred_note(
                tasks,
                reason="runtime_placement_incomplete",
                workspace_id=wid,
            )
            session.add_all(tasks)
            continue
        assert rt is not None
        try:
            orch = build_default_orchestrator_for_session(
                session,
                execution_node_key=str(rt.node_id).strip(),
                topology_id=int(rt.topology_id),  # type: ignore[arg-type]
            )
        except Exception as e:
            logger.warning(
                "cleanup_drain_orchestrator_binding_failed",
                extra={"workspace_id": wid, "error": str(e)},
                exc_info=True,
            )
            _merge_cleanup_deferred_note(
                tasks,
                reason="orchestrator_binding_failed",
                workspace_id=wid,
                extra={"error": str(e)[:500]},
            )
            session.add_all(tasks)
            continue
        total_succeeded += process_durable_cleanup_tasks_for_workspace(
            session,
            orch,
            ws,
            correlation_id="cleanup_drain",
        )
    return total_succeeded
