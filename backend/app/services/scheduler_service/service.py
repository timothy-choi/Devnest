"""Schedule workspaces onto execution nodes and produce human-readable explanations."""

from __future__ import annotations

import logging

from sqlmodel import Session

from app.services.placement_service.constants import (
    DEFAULT_WORKSPACE_REQUEST_CPU,
    DEFAULT_WORKSPACE_REQUEST_MEMORY_MB,
)
from app.services.placement_service.capacity import total_reserved_on_node_key
from app.services.placement_service.errors import InvalidPlacementParametersError, NoSchedulableNodeError
from app.services.placement_service.models import ExecutionNode
from app.libs.observability.log_events import LogEvent, log_event
from app.services.placement_service.node_placement import list_schedulable_nodes, reserve_node_for_workspace

from .models import WorkspaceComputeRequest, WorkspaceScheduleResult
from .policy import can_fit_workspace_effective, rank_candidate_nodes

logger = logging.getLogger(__name__)


def schedule_workspace(
    session: Session,
    *,
    workspace_id: int,
    requested_cpu: float = DEFAULT_WORKSPACE_REQUEST_CPU,
    requested_memory_mb: int = DEFAULT_WORKSPACE_REQUEST_MEMORY_MB,
) -> WorkspaceScheduleResult:
    """
    Reserve an execution node for a workspace-shaped workload (bring-up class jobs).

    Delegates row locking and selection to :func:`~app.services.placement_service.node_placement.reserve_node_for_workspace`.
    """
    try:
        node = reserve_node_for_workspace(
            session,
            workspace_id=workspace_id,
            requested_cpu=requested_cpu,
            requested_memory_mb=requested_memory_mb,
        )
        return WorkspaceScheduleResult(
            execution_node=node,
            insufficient_capacity=False,
            invalid_request=False,
            message="ok",
        )
    except InvalidPlacementParametersError as e:
        return WorkspaceScheduleResult(
            execution_node=None,
            insufficient_capacity=False,
            invalid_request=True,
            message=str(e),
        )
    except NoSchedulableNodeError as e:
        log_event(
            logger,
            LogEvent.PLACEMENT_NO_SCHEDULABLE_NODE,
            level=logging.WARNING,
            workspace_id=workspace_id,
            requested_cpu=requested_cpu,
            requested_memory_mb=requested_memory_mb,
            detail=str(e)[:2000],
        )
        return WorkspaceScheduleResult(
            execution_node=None,
            insufficient_capacity=True,
            invalid_request=False,
            message=str(e),
        )


def explain_placement_decision(
    session: Session,
    *,
    chosen: ExecutionNode,
    workspace_id: int,
    requested_cpu: float = DEFAULT_WORKSPACE_REQUEST_CPU,
    requested_memory_mb: int = DEFAULT_WORKSPACE_REQUEST_MEMORY_MB,
) -> str:
    """
    Summarize why ``chosen`` was selected among READY+schedulable nodes (best-effort; filter-only).

    ``workspace_id`` is accepted for future affinity context; unused in V1.
    """
    _ = workspace_id
    req = WorkspaceComputeRequest(requested_cpu=float(requested_cpu), requested_memory_mb=int(requested_memory_mb))
    pool = list_schedulable_nodes(session)
    ranked = rank_candidate_nodes(session, pool, req)
    used_c, used_m = total_reserved_on_node_key(session, (chosen.node_key or "").strip())
    free_c = max(0.0, float(chosen.allocatable_cpu or 0.0) - used_c)
    free_m = max(0, int(chosen.allocatable_memory_mb or 0) - used_m)
    lines: list[str] = [
        f"selected node_key={chosen.node_key!r} allocatable_cpu={chosen.allocatable_cpu} "
        f"allocatable_memory_mb={chosen.allocatable_memory_mb}",
        f"effective_free_cpu≈{free_c:.4f} effective_free_memory_mb≈{free_m} "
        f"(after workspace_runtime reservations on this node_key)",
        f"policy: maximize effective_free_cpu, then effective_free_memory_mb, then node_key ascending",
        f"READY+schedulable pool size (after devnest_node_provider filter): {len(pool)}",
        f"pool nodes satisfying effective capacity for cpu>={req.requested_cpu} "
        f"and memory_mb>={req.requested_memory_mb}: {len(ranked)}",
    ]
    if ranked:
        first = ranked[0]
        lines.append(f"rank-1 after policy: node_key={first.node_key!r}")
    if not can_fit_workspace_effective(free_c, free_m, req):
        lines.append(
            "warning: chosen node does not satisfy request per can_fit_workspace_effective (unexpected)",
        )
    return "\n".join(lines)
