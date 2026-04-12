"""Standard DevNest log event names (V1 observability).

Use :func:`log_event` so ``correlation_id`` and fields appear consistently in ``extra``.

TODO: Grafana/Loki queries on ``devnest_event``; alert on rate of ``reconcile.failed``,
``workspace.job.failed``, ``placement.no_schedulable_node``.
"""

from __future__ import annotations

import logging
from typing import Any

from .correlation import get_correlation_id


class LogEvent:
    """Stable event names (log record message = event name)."""

    WORKSPACE_INTENT_CREATED = "workspace.intent.created"
    WORKSPACE_JOB_QUEUED = "workspace.job.queued"
    WORKSPACE_JOB_STARTED = "workspace.job.started"
    WORKSPACE_JOB_SUCCEEDED = "workspace.job.succeeded"
    WORKSPACE_JOB_FAILED = "workspace.job.failed"

    ORCHESTRATOR_BRINGUP_STARTED = "orchestrator.bringup.started"
    ORCHESTRATOR_BRINGUP_SUCCEEDED = "orchestrator.bringup.succeeded"
    ORCHESTRATOR_BRINGUP_FAILED = "orchestrator.bringup.failed"

    GATEWAY_ROUTE_REGISTERED = "gateway.route.registered"
    GATEWAY_ROUTE_DEREGISTERED = "gateway.route.deregistered"

    RECONCILE_STARTED = "reconcile.started"
    RECONCILE_FIXED_RUNTIME = "reconcile.fixed_runtime"
    RECONCILE_FIXED_ROUTE = "reconcile.fixed_route"
    RECONCILE_FAILED = "reconcile.failed"

    PLACEMENT_NO_SCHEDULABLE_NODE = "placement.no_schedulable_node"

    AUTOSCALER_SCALE_UP_TRIGGERED = "autoscaler.scale_up.triggered"
    AUTOSCALER_SCALE_DOWN_TRIGGERED = "autoscaler.scale_down.triggered"

    EC2_NODE_PROVISIONED = "ec2.node.provisioned"
    EC2_NODE_TERMINATED = "ec2.node.terminated"


def log_event(
    logger: logging.Logger,
    name: str,
    *,
    level: int = logging.INFO,
    correlation_id: str | None = None,
    **fields: Any,
) -> None:
    """Emit structured log; ``correlation_id`` overrides contextvar (needed for sync FastAPI routes)."""
    out: dict[str, Any] = {k: v for k, v in fields.items() if v is not None}
    cid = (correlation_id or get_correlation_id() or "").strip() or None
    if cid:
        out["correlation_id"] = cid[:64]
    out["devnest_event"] = name
    logger.log(level, name, extra=out)
