"""Node capacity accounting: allocatable minus workspace runtime reservations (V1).

**Semantics**

- ``ExecutionNode.total_*``: catalog / hardware envelope (operator-defined).
- ``ExecutionNode.allocatable_*``: scheduler-visible capacity after node-level overhead (kube-style).
- **Reserved** capacity is **not** a column on ``ExecutionNode``; it is the sum of
  ``WorkspaceRuntime.reserved_*`` for rows with a non-empty ``node_id`` whose workspace **counts**
  toward holding a schedulable slot: not ``STOPPED``, ``DELETED``, or ``ERROR`` (ERROR releases
  capacity; the row may still show ``node_id`` for ops until cleared).
- **Effective free** = ``allocatable_* - reserved_sum``. SQL uses the raw difference (negative
  free excludes the node). Python helpers clamp when reporting diagnostics.

Placement uses correlated subqueries so concurrent workers still rely on ``FOR UPDATE`` on the
chosen ``execution_node`` row for the duration of the job transaction.

TODO: Tenant quotas, usage-based (cgroup) telemetry vs reservation, predictive scheduling.
"""

from __future__ import annotations

from sqlalchemy import and_, func
from sqlmodel import Session, select

from app.services.workspace_service.models import Workspace, WorkspaceRuntime
from app.services.workspace_service.models.enums import WorkspaceStatus

from .models import ExecutionNode

# Workspaces in these states do not consume schedulable capacity on their pinned node.
_WORKSPACE_STATUSES_EXCLUDED_FROM_RESERVATION_SUM = (
    WorkspaceStatus.STOPPED.value,
    WorkspaceStatus.DELETED.value,
    WorkspaceStatus.ERROR.value,
)


def _runtime_pin_predicates_for_subquery():
    """Pinned to this execution node row; ignore null/blank ``node_id`` (defensive)."""
    return and_(
        WorkspaceRuntime.node_id == ExecutionNode.node_key,
        WorkspaceRuntime.node_id.isnot(None),
        WorkspaceRuntime.node_id != "",
    )


def _runtime_pin_predicates_for_node_key(bind_key: str):
    """Filter workspace_runtime rows pinned to a concrete ``node_key`` (already non-empty)."""
    return and_(
        WorkspaceRuntime.node_id == bind_key,
        WorkspaceRuntime.node_id.isnot(None),
        WorkspaceRuntime.node_id != "",
    )


def reserved_cpu_sum_subquery():
    """Correlated scalar: sum of ``reserved_cpu`` on this node (non-stopped, non-deleted workspaces)."""
    return (
        select(func.coalesce(func.sum(WorkspaceRuntime.reserved_cpu), 0.0))
        .select_from(WorkspaceRuntime)
        .join(Workspace, Workspace.workspace_id == WorkspaceRuntime.workspace_id)
        .where(
            _runtime_pin_predicates_for_subquery(),
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
            _runtime_pin_predicates_for_subquery(),
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
            _runtime_pin_predicates_for_node_key(key),
            Workspace.status.not_in(_WORKSPACE_STATUSES_EXCLUDED_FROM_RESERVATION_SUM),
        )
    )
    row = session.exec(stmt).one()
    raw_cpu = row[0]
    raw_mem = row[1]
    try:
        cpu = float(raw_cpu) if raw_cpu is not None else 0.0
    except (TypeError, ValueError):
        cpu = 0.0
    try:
        mem = int(raw_mem) if raw_mem is not None else 0
    except (TypeError, ValueError):
        mem = 0
    return (max(0.0, cpu), max(0, mem))


def max_effective_free_resources_across_schedulable(
    session: Session,
    *,
    base_predicates: list,
) -> tuple[float, int]:
    """
    Best-effort max effective free (cpu, memory_mb) among nodes matching ``base_predicates``.

    Used for placement/autoscale diagnostics (small N).
    """
    if not base_predicates:
        return (0.0, 0)
    stmt = select(ExecutionNode).where(and_(*base_predicates))
    nodes = list(session.exec(stmt).all())
    best_cpu = 0.0
    best_mem = 0
    for n in nodes:
        k = (n.node_key or "").strip()
        if not k:
            continue
        used_cpu, used_mem = total_reserved_on_node_key(session, k)
        free_c = max(0.0, float(n.allocatable_cpu or 0.0) - used_cpu)
        free_m = max(0, int(n.allocatable_memory_mb or 0) - used_mem)
        best_cpu = max(best_cpu, free_c)
        best_mem = max(best_mem, free_m)
    return (best_cpu, best_mem)


def active_workload_count_subquery():
    """Correlated scalar subquery: count of active (capacity-consuming) workloads pinned to this node.

    Uses the same cohort as :func:`reserved_cpu_sum_subquery` (excludes STOPPED / DELETED / ERROR),
    so SQL-level spread-aware placement ordering stays consistent with capacity accounting.
    """
    return (
        select(func.count())
        .select_from(WorkspaceRuntime)
        .join(Workspace, Workspace.workspace_id == WorkspaceRuntime.workspace_id)
        .where(
            _runtime_pin_predicates_for_subquery(),
            Workspace.status.not_in(_WORKSPACE_STATUSES_EXCLUDED_FROM_RESERVATION_SUM),
        )
        .correlate(ExecutionNode)
    ).scalar_subquery()


def count_active_workloads_on_node_key(session: Session, node_key: str) -> int:
    """Count active (capacity-consuming) workloads pinned to ``node_key``.

    Mirrors the cohort used by :func:`total_reserved_on_node_key` — STOPPED, DELETED, and ERROR
    workspaces are excluded because they release scheduler capacity.
    """
    key = (node_key or "").strip()
    if not key:
        return 0
    stmt = (
        select(func.count())
        .select_from(WorkspaceRuntime)
        .join(Workspace, Workspace.workspace_id == WorkspaceRuntime.workspace_id)
        .where(
            _runtime_pin_predicates_for_node_key(key),
            Workspace.status.not_in(_WORKSPACE_STATUSES_EXCLUDED_FROM_RESERVATION_SUM),
        )
    )
    raw = session.exec(stmt).one()
    return int(raw[0] if isinstance(raw, tuple) else raw)


def max_effective_free_cpu_across_schedulable(session: Session, *, base_predicates: list) -> float:
    """
    Best-effort max effective free CPU among nodes matching ``base_predicates`` (for diagnostics).

    Uses Python-side evaluation after a light query — acceptable for admin/autoscale logging (small N).
    """
    cpu, _mem = max_effective_free_resources_across_schedulable(session, base_predicates=base_predicates)
    return cpu
