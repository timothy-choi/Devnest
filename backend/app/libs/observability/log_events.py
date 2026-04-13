"""Standard DevNest log event names (V1 observability).

Use :func:`log_event` so ``correlation_id`` and fields appear consistently in ``extra``.

TODO: Grafana/Loki queries on ``devnest_event``; alert on rate of ``reconcile.failed``,
``workspace.job.failed``, ``placement.no_schedulable_node``.
"""

from __future__ import annotations

import logging
from typing import Any

from .correlation import get_correlation_id

# ``logging.LogRecord`` reserves these attribute names; they must not appear in ``extra``.
_LOGRECORD_RESERVED_KEYS = frozenset(
    {
        "name",
        "msg",
        "args",
        "created",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "thread",
        "threadName",
        "exc_info",
        "exc_text",
        "stack_info",
        "asctime",
        "taskName",
    },
)


def _sanitize_extra(fields: dict[str, Any]) -> dict[str, Any]:
    """Map reserved keys so ``Logger.log(..., extra=...)`` does not raise KeyError."""
    out: dict[str, Any] = {}
    for k, v in fields.items():
        if v is None:
            continue
        nk = k
        if k == "message":
            nk = "detail"
        elif k == "asctime":
            nk = "log_asctime"
        elif k in _LOGRECORD_RESERVED_KEYS:
            nk = f"devnest_{k}"
        out[nk] = v
    return out


class LogEvent:
    """Stable event names (log record message = event name)."""

    WORKSPACE_INTENT_CREATED = "workspace.intent.created"
    WORKSPACE_JOB_QUEUED = "workspace.job.queued"
    WORKSPACE_JOB_STARTED = "workspace.job.started"
    WORKSPACE_JOB_SUCCEEDED = "workspace.job.succeeded"
    WORKSPACE_JOB_FAILED = "workspace.job.failed"
    WORKSPACE_JOB_RETRY_SCHEDULED = "workspace.job.retry_scheduled"
    WORKSPACE_JOB_RETRY_EXHAUSTED = "workspace.job.retry_exhausted"
    WORKSPACE_JOB_FAILED_TERMINAL = "workspace.job.failed_terminal"

    RECONCILE_RETRY_SCHEDULED = "reconcile.job.retry_scheduled"
    RECONCILE_FAILED_TERMINAL = "reconcile.job.failed_terminal"

    ORCHESTRATOR_BRINGUP_STARTED = "orchestrator.bringup.started"
    ORCHESTRATOR_BRINGUP_SUCCEEDED = "orchestrator.bringup.succeeded"
    ORCHESTRATOR_BRINGUP_FAILED = "orchestrator.bringup.failed"
    ORCHESTRATOR_SNAPSHOT_EXPORT_SUCCEEDED = "orchestrator.snapshot.export_succeeded"
    ORCHESTRATOR_SNAPSHOT_IMPORT_SUCCEEDED = "orchestrator.snapshot.import_succeeded"

    WORKSPACE_SNAPSHOT_CREATED = "workspace.snapshot.created"
    WORKSPACE_SNAPSHOT_FAILED = "workspace.snapshot.failed"
    WORKSPACE_SNAPSHOT_RESTORED = "workspace.snapshot.restored"
    WORKSPACE_SNAPSHOT_DELETED = "workspace.snapshot.deleted"

    GATEWAY_ROUTE_REGISTERED = "gateway.route.registered"
    GATEWAY_ROUTE_DEREGISTERED = "gateway.route.deregistered"

    RECONCILE_STARTED = "reconcile.started"
    RECONCILE_FIXED_RUNTIME = "reconcile.fixed_runtime"
    RECONCILE_FIXED_ROUTE = "reconcile.fixed_route"
    RECONCILE_FAILED = "reconcile.failed"

    PLACEMENT_NO_SCHEDULABLE_NODE = "placement.no_schedulable_node"

    SCHEDULER_NODE_SELECTED = "scheduler.node.selected"
    SCHEDULER_FAIRNESS_SPREAD_APPLIED = "scheduler.fairness_spread_applied"

    AUTOSCALER_SCALE_UP_TRIGGERED = "autoscaler.scale_up.triggered"
    AUTOSCALER_SCALE_UP_SUPPRESSED = "autoscaler.scale_up.suppressed"
    AUTOSCALER_SCALE_DOWN_TRIGGERED = "autoscaler.scale_down.triggered"
    AUTOSCALER_SCALE_DOWN_SUPPRESSED = "autoscaler.scale_down.suppressed"

    EC2_NODE_PROVISIONED = "ec2.node.provisioned"
    EC2_NODE_TERMINATED = "ec2.node.terminated"

    # Internal control-plane audit (who triggered sensitive HTTP surfaces; no secrets in fields).
    AUDIT_INTERNAL_WORKSPACE_JOBS_PROCESS = "audit.internal.workspace_jobs.process"
    AUDIT_INTERNAL_WORKSPACE_RECONCILE_RUNTIME = "audit.internal.workspace.reconcile_runtime"
    AUDIT_INTERNAL_AUTOSCALER_PROVISION_ONE = "audit.internal.autoscaler.provision_one"
    AUDIT_INTERNAL_AUTOSCALER_RECLAIM_ONE = "audit.internal.autoscaler.reclaim_one"
    AUDIT_INTERNAL_EXECUTION_NODES_MUTATION = "audit.internal.execution_nodes.mutation"
    AUDIT_INTERNAL_NOTIFICATIONS_CREATE = "audit.internal.notifications.create"
    AUDIT_INTERNAL_NOTIFICATIONS_RETRY_DELIVERY = "audit.internal.notifications.retry_delivery"

    SECURITY_INTERNAL_AUTH_FAILED = "security.internal.auth_failed"
    SECURITY_INTERNAL_NOT_CONFIGURED = "security.internal.not_configured"

    LIFESPAN_WORKER_STARTED = "lifespan.worker.started"
    LIFESPAN_WORKER_STOPPED = "lifespan.worker.stopped"
    LIFESPAN_WORKER_TICK = "lifespan.worker.tick"
    # Aliases used in lifespan_worker.py (maps to same event strings as above).
    WORKSPACE_JOB_WORKER_STARTED = "lifespan.worker.started"
    WORKSPACE_JOB_WORKER_STOPPED = "lifespan.worker.stopped"
    WORKSPACE_JOB_WORKER_TICK = "lifespan.worker.tick"

    WORKSPACE_SESSION_CREATED = "workspace.session.created"
    WORKSPACE_SESSION_REFRESHED = "workspace.session.refreshed"
    WORKSPACE_SESSION_EXPIRED = "workspace.session.expired"
    WORKSPACE_SESSION_REVOKED_BULK = "workspace.session.revoked_bulk"
    WORKSPACE_ACCESS_DENIED = "workspace.access.denied"
    WORKSPACE_ACCESS_GRANTED = "workspace.access.granted"

    AUDIT_EVENT_RECORDED = "audit.event.recorded"

    GATEWAY_AUTH_ALLOWED = "gateway.auth.allowed"
    GATEWAY_AUTH_DENIED = "gateway.auth.denied"

    SNAPSHOT_STORAGE_UPLOAD_STARTED = "snapshot.storage.upload.started"
    SNAPSHOT_STORAGE_UPLOAD_SUCCEEDED = "snapshot.storage.upload.succeeded"
    SNAPSHOT_STORAGE_UPLOAD_FAILED = "snapshot.storage.upload.failed"
    SNAPSHOT_STORAGE_DOWNLOAD_STARTED = "snapshot.storage.download.started"
    SNAPSHOT_STORAGE_DOWNLOAD_SUCCEEDED = "snapshot.storage.download.succeeded"
    SNAPSHOT_STORAGE_DOWNLOAD_FAILED = "snapshot.storage.download.failed"


def log_event(
    logger: logging.Logger,
    name: str,
    *,
    level: int = logging.INFO,
    correlation_id: str | None = None,
    **fields: Any,
) -> None:
    """Emit structured log; ``correlation_id`` overrides contextvar (needed for sync FastAPI routes)."""
    out = _sanitize_extra(dict(fields))
    cid = (correlation_id or get_correlation_id() or "").strip() or None
    if cid:
        out["correlation_id"] = cid[:64]
    out["devnest_event"] = name
    logger.log(level, name, extra=out)
