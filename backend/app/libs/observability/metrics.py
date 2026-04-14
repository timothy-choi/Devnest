"""
Prometheus metrics (MVP). Gauges are refreshed from the DB on each ``/metrics`` scrape.

TODO: Grafana dashboards, alert rules, histograms for job duration, RED/USE SLOs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

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

AUTOSCALER_SCALE_DOWN_TOTAL = Counter(
    "devnest_autoscaler_scale_down",
    "EC2 scale-down operations that invoked terminate",
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


def record_internal_auth_failure(*, scope: str) -> None:
    INTERNAL_AUTH_FAILURES_TOTAL.labels(scope=scope or "unknown").inc()


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
