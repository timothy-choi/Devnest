"""Node capacity accounting: allocatable minus workspace runtime reservations (V1).

**Semantics**

- ``ExecutionNode.total_*``: catalog / hardware envelope (operator-defined).
- ``ExecutionNode.allocatable_*``: scheduler-visible capacity after node-level overhead (kube-style).
- **Reserved** capacity is **not** a column on ``ExecutionNode``; it is the sum of
  ``WorkspaceRuntime.reserved_*`` for rows pinned to ``node_key`` whose workspace is neither
  ``STOPPED`` nor ``DELETED``.
- **Effective free** = ``allocatable_* - reserved_sum`` (clamped to ``>= 0`` in application logic;
  SQL comparisons use the raw difference).

Placement uses correlated subqueries so concurrent workers still rely on ``FOR UPDATE`` on the
chosen ``execution_node`` row for the duration of the job transaction.

TODO: Tenant quotas, usage-based (cgroup) telemetry vs reservation, predictive scheduling.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlmodel import Session, select

from app.services.workspace_service.models import Workspace, WorkspaceRuntime
from app.services.workspace_service.models.enums import WorkspaceStatus

from .models import ExecutionNode

# Workspaces in these states do not consume schedulable capacity on their pinned node.
_WORKSPACE_STATUSES_EXCLUDED_FROM_RESERVATION_SUM = (
    WorkspaceStatus.STOPPED.value,
    WorkspaceStatus.DELETED.value,
)


def reserved_cpu_sum_subquery():
    """Correlated scalar: sum of ``reserved_cpu`` on this node (non-stopped, non-deleted workspaces)."""
    return (
        select(func.coalesce(func.sum(WorkspaceRuntime.reserved_cpu), 0.0))
        .select_from(WorkspaceRuntime)
        .join(Workspace, Workspace.workspace_id == WorkspaceRuntime.workspace_id)
        .where(
            WorkspaceRuntime.node_id == ExecutionNode.node_key,
            Workspace.status.not_in(_WORKSPACE_STATUSES_EXCLUDED_FROM_RESERVATION_SUM),
        )
        .correlate(ExecutionNode)
    ).scalar_subquery()


def reserved_memory_sum_subquery():
    """Correlated scalar: sum of ``reserved_memory_mb`` for the same cohort."""
    return (
        select(func.coalesce(func.sum(WorkspaceRuntime.reserved_memory_mb), 0))
        .select_from(WorkspaceRuntime)
        .join(Workspace, Workspace.workspace_id == WorkspaceRuntime.workspace_id)
        .where(
            WorkspaceRuntime.node_id == ExecutionNode.node_key,
            Workspace.status.not_in(_WORKSPACE_STATUSES_EXCLUDED_FROM_RESERVATION_SUM),
        )
        .correlate(ExecutionNode)
    ).scalar_subquery()


def effective_free_cpu_expr():
    """SQL expression: allocatable_cpu minus reserved sum for this node_key."""
    return ExecutionNode.allocatable_cpu - reserved_cpu_sum_subquery()


def effective_free_memory_mb_expr():
    """SQL expression: allocatable_memory_mb minus reserved memory sum."""
    return ExecutionNode.allocatable_memory_mb - reserved_memory_sum_subquery()


def total_reserved_on_node_key(session: Session, node_key: str) -> tuple[float, int]:
    """Sum ``(reserved_cpu, reserved_memory_mb)`` for workloads counting against ``node_key``."""
    key = (node_key or "").strip()
    if not key:
        return (0.0, 0)
    stmt = (
        select(
            func.coalesce(func.sum(WorkspaceRuntime.reserved_cpu), 0.0),
            func.coalesce(func.sum(WorkspaceRuntime.reserved_memory_mb), 0),
        )
        .select_from(WorkspaceRuntime)
        .join(Workspace, Workspace.workspace_id == WorkspaceRuntime.workspace_id)
        .where(
            WorkspaceRuntime.node_id == key,
            Workspace.status.not_in(_WORKSPACE_STATUSES_EXCLUDED_FROM_RESERVATION_SUM),
        )
    )
    row = session.exec(stmt).one()
    cpu = float(row[0] if isinstance(row[0], float) else row[0])
    mem = int(row[1] if row[1] is not None else 0)
    return (cpu, mem)


def max_effective_free_cpu_across_schedulable(session: Session, *, base_predicates: list) -> float:
    """
    Best-effort max effective free CPU among nodes matching ``base_predicates`` (for diagnostics).

    Uses Python-side evaluation after a light query — acceptable for admin/autoscale logging (small N).
    """
    from sqlalchemy import and_

    if not base_predicates:
        return 0.0
    stmt = select(ExecutionNode).where(and_(*base_predicates))
    nodes = list(session.exec(stmt).all())
    best = 0.0
    for n in nodes:
        k = (n.node_key or "").strip()
        if not k:
            continue
        used_cpu, _ = total_reserved_on_node_key(session, k)
        free = max(0.0, float(n.allocatable_cpu or 0.0) - used_cpu)
        best = max(best, free)
    return best
