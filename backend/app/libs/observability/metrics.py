"""
Prometheus metrics for DevNest autoscaling, scheduling, and reliability.

Gauges are refreshed from the DB on each ``GET /metrics`` scrape (see
:func:`refresh_gauges_from_db`). Counters and histograms are updated from worker,
autoscaler, orchestrator, and infrastructure paths.

``devnest_autoscaler_scale_up_total`` / ``devnest_autoscaler_scale_down_total`` are produced by
the Python client from counter names ``devnest_autoscaler_scale_up`` and
``devnest_autoscaler_scale_down``; both use the ``provider_type`` label (default ``ec2`` in
callers).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from app.services.placement_service.models import ExecutionNode, ExecutionNodeProviderType
from app.services.placement_service.models.enums import ExecutionNodeStatus
from app.services.workspace_service.models import Workspace, WorkspaceJob
from app.services.workspace_service.models.enums import WorkspaceJobStatus, WorkspaceStatus

if TYPE_CHECKING:
    from sqlmodel import Session


def _norm_label(value: str | None, *, max_len: int = 64, default: str = "unknown") -> str:
    s = (value or "").strip() or default
    return s[:max_len]

JOBS_TOTAL = Counter(
    "devnest_jobs",
    "Workspace jobs reaching a terminal status",
    ["job_type", "status"],
)

JOB_FAILURES_TOTAL = Counter(
    "devnest_job_failures",
    "Workspace jobs that ended in FAILED",
    ["job_type"],
)

PLACEMENT_FAILURES_TOTAL = Counter(
    "devnest_placement_failures",
    "Placement failures while dequeuing a job",
    ["reason"],
)

AUTOSCALER_SCALE_UP_TOTAL = Counter(
    "devnest_autoscaler_scale_up",
    "Autoscaler scale-up provisions started",
    ["provider_type"],
)

AUTOSCALER_DECISIONS_TOTAL = Counter(
    "devnest_autoscaler_decisions",
    "Autoscaler evaluation decisions",
    ["action", "scale_out_recommended"],
)

AUTOSCALER_PROVISIONS_TOTAL = Counter(
    "devnest_autoscaler_provisions",
    "Autoscaler provision attempts",
    ["result"],
)

AUTOSCALER_SCALE_DOWN_TOTAL = Counter(
    "devnest_autoscaler_scale_down",
    "Autoscaler scale-down operations that invoked EC2 terminate",
    ["provider_type"],
)

WORKSPACE_LIFECYCLE_FAILURES_TOTAL = Counter(
    "devnest_workspace_lifecycle_failures",
    "Workspace lifecycle failures by operation and failure code",
    ["operation", "failure_code"],
)

WORKSPACE_SNAPSHOT_OPERATIONS_TOTAL = Counter(
    "devnest_workspace_snapshot_operations",
    "Workspace snapshot operations by operation and result",
    ["operation", "result"],
)

WORKSPACE_PROVISIONING_DURATION_SECONDS = Histogram(
    "devnest_workspace_provisioning_duration_seconds",
    "Workspace runtime provisioning duration in seconds",
    ["job_type", "result"],
    buckets=(0.5, 1, 2.5, 5, 10, 20, 40, 60, 120, 300, 600),
)

GATEWAY_ROUTE_OPS_TOTAL = Counter(
    "devnest_gateway_route_operations",
    "Route-admin register/deregister outcomes",
    ["operation", "result"],
)

RECONCILE_OPS_TOTAL = Counter(
    "devnest_reconcile_operations",
    "Reconcile-runtime jobs reaching terminal status",
    ["result"],
)

BRINGUP_ROLLBACK_TOTAL = Counter(
    "devnest_orchestrator_bringup_rollback_total",
    "Compensating rollbacks after failed or aborted workspace bring-up",
    ["reason"],
)

BRINGUP_ROLLBACK_FAILED_TOTAL = Counter(
    "devnest_orchestrator_bringup_rollback_failed_total",
    "Bring-up rollback where inner stop/detach did not fully succeed after retries",
)

RECONCILE_LOCK_CONTENDED_TOTAL = Counter(
    "devnest_reconcile_lock_contended_total",
    "Reconcile jobs that could not acquire the per-workspace advisory lock",
)

RECONCILE_LOCK_HELD_TOTAL = Counter(
    "devnest_reconcile_lock_acquired_total",
    "Reconcile jobs that acquired the per-workspace advisory lock",
)

TOPOLOGY_JANITOR_ACTIONS_TOTAL = Counter(
    "devnest_topology_janitor_actions_total",
    "Topology janitor repairs",
    ["kind"],
)

CLEANUP_TASK_ENQUEUED_TOTAL = Counter(
    "devnest_cleanup_task_enqueued_total",
    "Durable cleanup tasks inserted or refreshed",
    ["scope"],
)

CLEANUP_TASK_ATTEMPT_TOTAL = Counter(
    "devnest_cleanup_task_attempt_total",
    "Durable cleanup stop attempts",
    ["scope", "result"],
)

INTERNAL_AUTH_FAILURES_TOTAL = Counter(
    "devnest_internal_auth_failures",
    "Rejected internal API requests (missing/invalid X-Internal-API-Key)",
    ["scope"],
)

JOBS_QUEUED_TOTAL = Counter(
    "devnest_jobs_queued",
    "Workspace jobs enqueued",
    ["job_type"],
)

QUEUE_DEPTH = Gauge(
    "devnest_queue_depth",
    "Count of workspace_job rows in QUEUED status",
)

WORKSPACE_STATES = Gauge(
    "devnest_workspace_states",
    "Workspaces by status label",
    ["status"],
)

NODE_STATES = Gauge(
    "devnest_node_states",
    "Execution nodes by status",
    ["status"],
)

EXECUTION_NODE_COUNTS = Gauge(
    "devnest_execution_nodes",
    "Execution nodes by status and provider type",
    ["status", "provider_type"],
)

EC2_NODE_STATES = Gauge(
    "devnest_ec2_nodes",
    "EC2 execution_node rows by status",
    ["status"],
)

# --- Production-style names (dashboards / alerts) -------------------------------------------

WORKSPACE_CREATED_TOTAL = Counter(
    "devnest_workspace_created_total",
    "Workspace create intents accepted (CREATE job queued)",
    ["workspace_status", "provider_type"],
)

WORKSPACE_FAILED_TOTAL = Counter(
    "devnest_workspace_failed_total",
    "Workspace jobs that reached terminal FAILED",
    ["workspace_status", "failure_reason", "node_key", "provider_type"],
)

WORKSPACE_RETRIED_TOTAL = Counter(
    "devnest_workspace_retried_total",
    "Workspace jobs scheduled for retry (backoff / requeue)",
    ["workspace_status", "failure_reason", "node_key", "provider_type"],
)

NODE_CLEANUP_TOTAL = Counter(
    "devnest_node_cleanup_total",
    "Cleanup / janitor / reconcile actions on execution topology or runtime debt",
    ["action"],
)

CHAOS_RECOVERY_TOTAL = Counter(
    "devnest_chaos_recovery_total",
    "Successful workspace job completions after at least one prior run attempt (retry recovery)",
    ["recovery_type", "job_type"],
)

ACTIVE_WORKSPACES = Gauge(
    "devnest_active_workspaces",
    "Workspaces in RUNNING status",
)

READY_NODES_COUNT = Gauge(
    "devnest_ready_nodes",
    "Execution nodes in READY status (all provider types)",
)

PROVISIONING_NODES_COUNT = Gauge(
    "devnest_provisioning_nodes",
    "Execution nodes in PROVISIONING status",
)

DRAINING_NODES_COUNT = Gauge(
    "devnest_draining_nodes",
    "Execution nodes in DRAINING status",
)

PENDING_WORKSPACE_JOBS = Gauge(
    "devnest_pending_workspace_jobs",
    "Workspace jobs waiting in QUEUED status",
)

NODE_DISK_FREE_MB = Gauge(
    "devnest_node_disk_free_mb",
    "Last reported disk free MiB per execution node (heartbeat)",
    ["node_key", "provider_type"],
)

NODE_MEMORY_FREE_MB = Gauge(
    "devnest_node_memory_free_mb",
    "Last reported memory free MiB per execution node (heartbeat)",
    ["node_key", "provider_type"],
)

WORKSPACE_PROVISION_SECONDS = Histogram(
    "devnest_workspace_provision_seconds",
    "Wall clock seconds for workspace runtime bring-up in CREATE/START worker paths",
    ["job_type", "workspace_status", "failure_reason"],
    buckets=(0.5, 1, 2.5, 5, 10, 20, 40, 60, 120, 300, 600),
)

NODE_BOOTSTRAP_SECONDS = Histogram(
    "devnest_node_bootstrap_seconds",
    "Seconds from EC2 provision timestamp metadata to READY promotion when observed",
    ["node_key", "provider_type", "readiness"],
    buckets=(5, 15, 30, 60, 120, 300, 600, 1200, 2400, 3600, 7200),
)

SCALE_DOWN_SECONDS = Histogram(
    "devnest_scale_down_seconds",
    "Wall clock seconds for autoscaler drain + EC2 terminate path",
    ["node_key", "provider_type"],
    buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300, 600),
)

SSM_COMMAND_SECONDS = Histogram(
    "devnest_ssm_command_seconds",
    "Latency of SSM RunShellScript commands issued by DevNest",
    ["command_family"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)


def record_job_queued(job_type: str) -> None:
    JOBS_QUEUED_TOTAL.labels(job_type=job_type or "unknown").inc()


def record_job_terminal(*, job_type: str, status: str) -> None:
    jt = job_type or "unknown"
    st = status or "unknown"
    JOBS_TOTAL.labels(job_type=jt, status=st).inc()
    if st == WorkspaceJobStatus.FAILED.value:
        JOB_FAILURES_TOTAL.labels(job_type=jt).inc()


def record_placement_failure(*, reason: str) -> None:
    PLACEMENT_FAILURES_TOTAL.labels(reason=reason or "unknown").inc()


def record_autoscaler_scale_up(*, provider_type: str | None = None) -> None:
    pt = _norm_label(provider_type, default="ec2")
    AUTOSCALER_SCALE_UP_TOTAL.labels(provider_type=pt).inc()
    record_autoscaler_provision(result="success")


def record_autoscaler_decision(*, action: str, scale_out_recommended: bool) -> None:
    AUTOSCALER_DECISIONS_TOTAL.labels(
        action=(action or "unknown"),
        scale_out_recommended="true" if scale_out_recommended else "false",
    ).inc()


def record_autoscaler_provision(*, result: str) -> None:
    AUTOSCALER_PROVISIONS_TOTAL.labels(result=(result or "unknown")).inc()


def record_autoscaler_scale_down(*, provider_type: str | None = None) -> None:
    pt = _norm_label(provider_type, default="ec2")
    AUTOSCALER_SCALE_DOWN_TOTAL.labels(provider_type=pt).inc()


def record_gateway_operation(*, operation: str, success: bool) -> None:
    GATEWAY_ROUTE_OPS_TOTAL.labels(
        operation=operation,
        result="success" if success else "error",
    ).inc()


def record_reconcile_terminal(*, succeeded: bool) -> None:
    RECONCILE_OPS_TOTAL.labels(result="succeeded" if succeeded else "failed").inc()


def record_bringup_rollback(*, reason: str) -> None:
    """Low-cardinality reason: ``exception`` (bring-up raised) or ``probe_unhealthy``."""
    r = (reason or "unknown").strip().lower()
    if r not in ("exception", "probe_unhealthy"):
        r = "exception"
    BRINGUP_ROLLBACK_TOTAL.labels(reason=r).inc()


def record_bringup_rollback_failed() -> None:
    """Incremented when rollback stop/detach still fails after bounded retries."""
    BRINGUP_ROLLBACK_FAILED_TOTAL.inc()


def record_reconcile_lock_contended() -> None:
    RECONCILE_LOCK_CONTENDED_TOTAL.inc()


def record_reconcile_lock_acquired() -> None:
    RECONCILE_LOCK_HELD_TOTAL.inc()


def record_topology_janitor_action(*, kind: str) -> None:
    k = kind or "unknown"
    TOPOLOGY_JANITOR_ACTIONS_TOTAL.labels(kind=k).inc()
    NODE_CLEANUP_TOTAL.labels(action=f"topology_janitor:{k}").inc()


def record_cleanup_task_enqueued(*, scope: str) -> None:
    CLEANUP_TASK_ENQUEUED_TOTAL.labels(scope=scope or "unknown").inc()


def record_cleanup_task_attempt(*, scope: str, result: str) -> None:
    sc = scope or "unknown"
    res = result or "unknown"
    CLEANUP_TASK_ATTEMPT_TOTAL.labels(scope=sc, result=res).inc()
    if res == "succeeded":
        NODE_CLEANUP_TOTAL.labels(action=f"cleanup_task:{sc}").inc()


def record_internal_auth_failure(*, scope: str) -> None:
    INTERNAL_AUTH_FAILURES_TOTAL.labels(scope=scope or "unknown").inc()


def record_workspace_lifecycle_failure(*, operation: str, failure_code: str | None = None) -> None:
    WORKSPACE_LIFECYCLE_FAILURES_TOTAL.labels(
        operation=operation or "unknown",
        failure_code=failure_code or "unknown",
    ).inc()


def record_workspace_snapshot_operation(*, operation: str, result: str) -> None:
    WORKSPACE_SNAPSHOT_OPERATIONS_TOTAL.labels(
        operation=operation or "unknown",
        result=result or "unknown",
    ).inc()


def observe_workspace_provisioning_duration(
    *,
    job_type: str,
    result: str,
    duration_seconds: float,
    workspace_status: str | None = None,
    failure_reason: str | None = None,
) -> None:
    jt = job_type or "unknown"
    res = result or "unknown"
    ws_st = _norm_label(workspace_status)
    fr = _norm_label(failure_reason, default="none") if res != "success" else "none"
    WORKSPACE_PROVISIONING_DURATION_SECONDS.labels(
        job_type=jt,
        result=res,
    ).observe(max(0.0, float(duration_seconds)))
    WORKSPACE_PROVISION_SECONDS.labels(
        job_type=jt,
        workspace_status=ws_st,
        failure_reason=fr,
    ).observe(max(0.0, float(duration_seconds)))


def record_workspace_created(
    *,
    workspace_status: str | None = None,
    provider_type: str | None = None,
) -> None:
    WORKSPACE_CREATED_TOTAL.labels(
        workspace_status=_norm_label(workspace_status, default="pending"),
        provider_type=_norm_label(provider_type, default="unknown"),
    ).inc()


def record_workspace_failed(
    *,
    workspace_status: str | None = None,
    failure_reason: str | None = None,
    node_key: str | None = None,
    provider_type: str | None = None,
) -> None:
    WORKSPACE_FAILED_TOTAL.labels(
        workspace_status=_norm_label(workspace_status),
        failure_reason=_norm_label(failure_reason),
        node_key=_norm_label(node_key),
        provider_type=_norm_label(provider_type),
    ).inc()


def record_workspace_retried(
    *,
    workspace_status: str | None = None,
    failure_reason: str | None = None,
    node_key: str | None = None,
    provider_type: str | None = None,
) -> None:
    WORKSPACE_RETRIED_TOTAL.labels(
        workspace_status=_norm_label(workspace_status),
        failure_reason=_norm_label(failure_reason),
        node_key=_norm_label(node_key),
        provider_type=_norm_label(provider_type),
    ).inc()


def record_chaos_recovery(*, job_type: str | None = None, recovery_type: str | None = None) -> None:
    CHAOS_RECOVERY_TOTAL.labels(
        recovery_type=_norm_label(recovery_type, default="retry_success"),
        job_type=_norm_label(job_type),
    ).inc()


def observe_node_bootstrap_seconds(
    *,
    duration_seconds: float,
    node_key: str | None = None,
    provider_type: str | None = None,
    readiness: str | None = None,
) -> None:
    NODE_BOOTSTRAP_SECONDS.labels(
        node_key=_norm_label(node_key),
        provider_type=_norm_label(provider_type, default="ec2"),
        readiness=_norm_label(readiness, default="ready"),
    ).observe(max(0.0, float(duration_seconds)))


def observe_scale_down_seconds(
    *,
    duration_seconds: float,
    node_key: str | None = None,
    provider_type: str | None = None,
) -> None:
    SCALE_DOWN_SECONDS.labels(
        node_key=_norm_label(node_key),
        provider_type=_norm_label(provider_type, default="ec2"),
    ).observe(max(0.0, float(duration_seconds)))


def observe_ssm_command_seconds(*, duration_seconds: float, command_family: str | None = None) -> None:
    SSM_COMMAND_SECONDS.labels(command_family=_norm_label(command_family, default="shell")).observe(
        max(0.0, float(duration_seconds))
    )


def record_node_cleanup(*, action: str, amount: float = 1.0) -> None:
    amt = max(0.0, float(amount))
    if amt <= 0:
        return
    NODE_CLEANUP_TOTAL.labels(action=_norm_label(action)).inc(amt)


def refresh_gauges_from_db(session: Session) -> None:
    from sqlalchemy import func
    from sqlmodel import select

    q_stmt = (
        select(func.count())
        .select_from(WorkspaceJob)
        .where(WorkspaceJob.status == WorkspaceJobStatus.QUEUED.value)
    )
    raw = session.exec(q_stmt).one()
    qn = int(raw[0] if isinstance(raw, tuple) else raw)
    QUEUE_DEPTH.set(qn)
    PENDING_WORKSPACE_JOBS.set(qn)

    active_stmt = (
        select(func.count())
        .select_from(Workspace)
        .where(Workspace.status == WorkspaceStatus.RUNNING.value)
    )
    aw_raw = session.exec(active_stmt).one()
    ACTIVE_WORKSPACES.set(int(aw_raw[0] if isinstance(aw_raw, tuple) else aw_raw))

    ready_stmt = (
        select(func.count())
        .select_from(ExecutionNode)
        .where(ExecutionNode.status == ExecutionNodeStatus.READY.value)
    )
    r_raw = session.exec(ready_stmt).one()
    READY_NODES_COUNT.set(int(r_raw[0] if isinstance(r_raw, tuple) else r_raw))

    prov_stmt = (
        select(func.count())
        .select_from(ExecutionNode)
        .where(ExecutionNode.status == ExecutionNodeStatus.PROVISIONING.value)
    )
    p_raw = session.exec(prov_stmt).one()
    PROVISIONING_NODES_COUNT.set(int(p_raw[0] if isinstance(p_raw, tuple) else p_raw))

    drain_stmt = (
        select(func.count())
        .select_from(ExecutionNode)
        .where(ExecutionNode.status == ExecutionNodeStatus.DRAINING.value)
    )
    d_raw = session.exec(drain_stmt).one()
    DRAINING_NODES_COUNT.set(int(d_raw[0] if isinstance(d_raw, tuple) else d_raw))

    for s in WorkspaceStatus:
        WORKSPACE_STATES.labels(status=s.value).set(0)
    ws_stmt = select(Workspace.status, func.count()).group_by(Workspace.status)
    for row in session.exec(ws_stmt).all():
        st, cnt = row[0], row[1]
        if st:
            WORKSPACE_STATES.labels(status=str(st)).set(int(cnt))

    for es in ExecutionNodeStatus:
        NODE_STATES.labels(status=es.value).set(0)
    node_stmt = select(ExecutionNode.status, func.count()).group_by(ExecutionNode.status)
    for row in session.exec(node_stmt).all():
        st, cnt = row[0], row[1]
        if st:
            NODE_STATES.labels(status=str(st)).set(int(cnt))

    provider_values = [e.value for e in ExecutionNodeProviderType]
    for es in ExecutionNodeStatus:
        for provider in provider_values:
            EXECUTION_NODE_COUNTS.labels(status=es.value, provider_type=provider).set(0)
    provider_stmt = (
        select(ExecutionNode.status, ExecutionNode.provider_type, func.count())
        .group_by(ExecutionNode.status, ExecutionNode.provider_type)
    )
    for row in session.exec(provider_stmt).all():
        st, provider, cnt = row[0], row[1], row[2]
        if st and provider:
            EXECUTION_NODE_COUNTS.labels(status=str(st), provider_type=str(provider)).set(int(cnt))

    for es in ExecutionNodeStatus:
        EC2_NODE_STATES.labels(status=es.value).set(0)
    ec2_stmt = (
        select(ExecutionNode.status, func.count())
        .where(ExecutionNode.provider_type == ExecutionNodeProviderType.EC2.value)
        .group_by(ExecutionNode.status)
    )
    for row in session.exec(ec2_stmt).all():
        st, cnt = row[0], row[1]
        if st:
            EC2_NODE_STATES.labels(status=str(st)).set(int(cnt))

    nodes_stmt = select(ExecutionNode)
    for node in session.exec(nodes_stmt).all():
        nk = _norm_label(getattr(node, "node_key", None), default="unknown")
        pt = _norm_label(
            getattr(node, "provider_type", None),
            default="unknown",
        )
        disk_mb = getattr(node, "disk_free_mb", None)
        mem_mb = getattr(node, "memory_free_mb", None)
        if disk_mb is not None:
            NODE_DISK_FREE_MB.labels(node_key=nk, provider_type=pt).set(float(disk_mb))
        if mem_mb is not None:
            NODE_MEMORY_FREE_MB.labels(node_key=nk, provider_type=pt).set(float(mem_mb))


def metrics_response_body() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
