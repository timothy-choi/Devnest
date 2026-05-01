"""
Prometheus metrics (MVP). Gauges are refreshed from the DB on each ``/metrics`` scrape.

TODO: Grafana dashboards, alert rules, histograms for job duration, RED/USE SLOs.
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
    "EC2 scale-up provisions started by autoscaler",
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
    "EC2 scale-down operations that invoked terminate",
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


def record_autoscaler_scale_up() -> None:
    AUTOSCALER_SCALE_UP_TOTAL.inc()
    record_autoscaler_provision(result="success")


def record_autoscaler_decision(*, action: str, scale_out_recommended: bool) -> None:
    AUTOSCALER_DECISIONS_TOTAL.labels(
        action=(action or "unknown"),
        scale_out_recommended="true" if scale_out_recommended else "false",
    ).inc()


def record_autoscaler_provision(*, result: str) -> None:
    AUTOSCALER_PROVISIONS_TOTAL.labels(result=(result or "unknown")).inc()


def record_autoscaler_scale_down() -> None:
    AUTOSCALER_SCALE_DOWN_TOTAL.inc()


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
    TOPOLOGY_JANITOR_ACTIONS_TOTAL.labels(kind=kind or "unknown").inc()


def record_cleanup_task_enqueued(*, scope: str) -> None:
    CLEANUP_TASK_ENQUEUED_TOTAL.labels(scope=scope or "unknown").inc()


def record_cleanup_task_attempt(*, scope: str, result: str) -> None:
    CLEANUP_TASK_ATTEMPT_TOTAL.labels(scope=scope or "unknown", result=result or "unknown").inc()


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


def observe_workspace_provisioning_duration(*, job_type: str, result: str, duration_seconds: float) -> None:
    WORKSPACE_PROVISIONING_DURATION_SECONDS.labels(
        job_type=job_type or "unknown",
        result=result or "unknown",
    ).observe(max(0.0, float(duration_seconds)))


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


def metrics_response_body() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
