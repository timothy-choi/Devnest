"""
Pure scheduling policy for V1 execution nodes (effective free capacity).

**Sort policy (best-fit on effective free headroom, deterministic):** prefer higher
``effective_free_cpu``, then higher ``effective_free_memory_mb``, then lower ``node_key``
lexicographically.

Effective free = ``allocatable_*`` minus sums of ``WorkspaceRuntime.reserved_*`` for workloads on that
``node_key`` (workspace not ``STOPPED`` / ``DELETED``).

SQL placement ordering must stay aligned with
:func:`app.services.placement_service.node_placement.select_node_for_workspace`.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlmodel import Session

from app.services.placement_service.capacity import total_reserved_on_node_key
from app.services.placement_service.models import ExecutionNode, ExecutionNodeStatus

from .models import WorkspaceComputeRequest


def scheduling_sort_key_effective(free_cpu: float, free_mem: int, node_key: str) -> tuple[float, int, str]:
    """Descending effective CPU/RAM, ascending node_key — mirrors SQL placement ordering."""
    return (-float(free_cpu), -int(free_mem), (node_key or "").strip())


def scheduling_sort_key(node: ExecutionNode) -> tuple[float, int, str]:
    """Legacy: sort by allocatable_* only (ignores reservations). Prefer :func:`scheduling_sort_key_effective`."""
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
    """
    Filter to READY+schedulable nodes that fit ``req`` on **effective** free capacity, then sort.

    TODO: affinity / anti-affinity, topology-aware ranking, utilization from real usage signals.
    """
    scored: list[tuple[tuple[float, int, str], ExecutionNode]] = []
    for n in nodes:
        if not _is_v1_scheduling_candidate(n):
            continue
        k = (n.node_key or "").strip()
        used_c, used_m = total_reserved_on_node_key(session, k)
        free_c = max(0.0, float(n.allocatable_cpu or 0.0) - used_c)
        free_m = max(0, int(n.allocatable_memory_mb or 0) - used_m)
        if not can_fit_workspace_effective(free_c, free_m, req):
            continue
        scored.append((scheduling_sort_key_effective(free_c, free_m, k), n))
    scored.sort(key=lambda x: x[0])
    return [x[1] for x in scored]
