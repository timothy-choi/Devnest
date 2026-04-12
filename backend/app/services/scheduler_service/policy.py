"""
Pure scheduling policy for V1 execution nodes.

**Sort policy (best-fit on allocatable headroom, deterministic):** prefer higher
``allocatable_cpu``, then higher ``allocatable_memory_mb``, then lower ``node_key`` lexicographically.

This must stay aligned with ``ORDER BY`` in
:func:`app.services.placement_service.node_placement.select_node_for_workspace`.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.services.placement_service.models import ExecutionNode, ExecutionNodeStatus

from .models import WorkspaceComputeRequest


def scheduling_sort_key(node: ExecutionNode) -> tuple[float, int, str]:
    """Descending CPU/RAM, ascending node_key — mirrors SQL placement ordering."""
    cpu = float(node.allocatable_cpu or 0.0)
    mem = int(node.allocatable_memory_mb or 0)
    key = (node.node_key or "").strip()
    return (-cpu, -mem, key)


def can_fit_workspace(node: ExecutionNode, req: WorkspaceComputeRequest) -> bool:
    """True if allocatable CPU/RAM can satisfy the workspace-shaped request (filter-only)."""
    if float(node.allocatable_cpu or 0) < float(req.requested_cpu):
        return False
    if int(node.allocatable_memory_mb or 0) < int(req.requested_memory_mb):
        return False
    return True


def _is_v1_scheduling_candidate(node: ExecutionNode) -> bool:
    """Matches placement gate: READY and schedulable (defense in depth for explain / future callers)."""
    return bool(node.schedulable) and (node.status or "").strip() == ExecutionNodeStatus.READY.value


def rank_candidate_nodes(
    nodes: Sequence[ExecutionNode],
    req: WorkspaceComputeRequest,
) -> list[ExecutionNode]:
    """
    Filter to READY+schedulable nodes that fit ``req``, then sort by :func:`scheduling_sort_key`.

    TODO: affinity / anti-affinity, topology-aware ranking, utilization from real usage signals.
    """
    fitting = [n for n in nodes if _is_v1_scheduling_candidate(n) and can_fit_workspace(n, req)]
    return sorted(fitting, key=scheduling_sort_key)
