"""
Pure scheduling policy for V1 execution nodes (effective free capacity + spread fairness).

**Sort policy (spread-aware best-fit, deterministic):**
  1. Highest effective free CPU  (capacity-first: prevents fragmentation).
  2. Highest effective free memory.
  3. Fewest active workloads (fairness: anti-concentration / spread across nodes).
  4. Lowest node_key lexicographically (stable tiebreak).

"Active workload" = workspace not STOPPED / DELETED / ERROR, matching the capacity-accounting cohort.

SQL placement ordering in
:func:`app.services.placement_service.node_placement.select_node_for_workspace` must stay aligned
with this Python ordering so that ``explain_placement_decision`` accurately reflects real decisions.

TODO: affinity / anti-affinity rules, topology-zone awareness, per-user/per-workspace placement hints.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlmodel import Session

from app.services.placement_service.capacity import (
    count_active_workloads_on_node_key,
    total_reserved_on_node_key,
)
from app.services.placement_service.models import ExecutionNode, ExecutionNodeStatus

from .models import WorkspaceComputeRequest


def scheduling_sort_key_spread(
    free_cpu: float,
    free_mem: int,
    workload_count: int,
    node_key: str,
) -> tuple[float, int, int, str]:
    """Spread-aware sort key: capacity-first, then fewer active workloads, then stable node_key.

    Preserves the capacity-first invariant so large workloads always land on the most capable node,
    while workload_count breaks ties to distribute evenly across similarly-loaded nodes.
    """
    return (-float(free_cpu), -int(free_mem), int(workload_count), (node_key or "").strip())


def scheduling_sort_key_effective(free_cpu: float, free_mem: int, node_key: str) -> tuple[float, int, str]:
    """Capacity-only sort key (no spread) — kept for backward compatibility and diagnostics."""
    return (-float(free_cpu), -int(free_mem), (node_key or "").strip())


def scheduling_sort_key(node: ExecutionNode) -> tuple[float, int, str]:
    """Legacy: sort by allocatable_* only (ignores reservations). Prefer :func:`scheduling_sort_key_spread`."""
    cpu = float(node.allocatable_cpu or 0.0)
    mem = int(node.allocatable_memory_mb or 0)
    key = (node.node_key or "").strip()
    return (-cpu, -mem, key)


def can_fit_workspace_effective(free_cpu: float, free_mem: int, req: WorkspaceComputeRequest) -> bool:
    """True if effective free CPU/RAM can satisfy the workspace-shaped request."""
    if float(free_cpu) < float(req.requested_cpu):
        return False
    if int(free_mem) < int(req.requested_memory_mb):
        return False
    return True


def can_fit_workspace(node: ExecutionNode, req: WorkspaceComputeRequest) -> bool:
    """True if **allocatable** CPU/RAM can satisfy the request (ignores reservations — tests / guards)."""
    if float(node.allocatable_cpu or 0) < float(req.requested_cpu):
        return False
    if int(node.allocatable_memory_mb or 0) < int(req.requested_memory_mb):
        return False
    return True


def _is_v1_scheduling_candidate(node: ExecutionNode) -> bool:
    """Matches placement gate: READY and schedulable (defense in depth for explain / future callers)."""
    return bool(node.schedulable) and (node.status or "").strip() == ExecutionNodeStatus.READY.value


def rank_candidate_nodes(
    session: Session,
    nodes: Sequence[ExecutionNode],
    req: WorkspaceComputeRequest,
) -> list[ExecutionNode]:
    """Filter to READY+schedulable nodes that fit ``req`` on effective free capacity, then sort.

    Uses the spread-aware sort (:func:`scheduling_sort_key_spread`) so that nodes with equal capacity
    prefer the one with fewer active workloads — consistent with SQL-level placement ordering.

    TODO: affinity / anti-affinity, topology-zone awareness.
    """
    scored: list[tuple[tuple[float, int, int, str], ExecutionNode]] = []
    for n in nodes:
        if not _is_v1_scheduling_candidate(n):
            continue
        k = (n.node_key or "").strip()
        used_c, used_m = total_reserved_on_node_key(session, k)
        free_c = max(0.0, float(n.allocatable_cpu or 0.0) - used_c)
        free_m = max(0, int(n.allocatable_memory_mb or 0) - used_m)
        if not can_fit_workspace_effective(free_c, free_m, req):
            continue
        wcount = count_active_workloads_on_node_key(session, k)
        scored.append((scheduling_sort_key_spread(free_c, free_m, wcount, k), n))
    scored.sort(key=lambda x: x[0])
    return [x[1] for x in scored]
