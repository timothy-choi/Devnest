"""Schedule workspaces onto execution nodes and produce human-readable explanations."""

from __future__ import annotations

import logging

from sqlmodel import Session

from app.services.placement_service.constants import (
    DEFAULT_WORKSPACE_REQUEST_CPU,
    DEFAULT_WORKSPACE_REQUEST_MEMORY_MB,
)
from app.services.placement_service.errors import InvalidPlacementParametersError, NoSchedulableNodeError
from app.services.placement_service.models import ExecutionNode
from app.services.placement_service.node_placement import list_schedulable_nodes, reserve_node_for_workspace

from .models import WorkspaceComputeRequest, WorkspaceScheduleResult
from .policy import can_fit_workspace, rank_candidate_nodes

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
        logger.warning(
            "schedule_workspace_insufficient_capacity",
            extra={
                "workspace_id": workspace_id,
                "requested_cpu": requested_cpu,
                "requested_memory_mb": requested_memory_mb,
                "detail": str(e)[:2000],
            },
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
    ranked = rank_candidate_nodes(pool, req)
    lines: list[str] = [
        f"selected node_key={chosen.node_key!r} allocatable_cpu={chosen.allocatable_cpu} "
        f"allocatable_memory_mb={chosen.allocatable_memory_mb}",
        f"policy: maximize allocatable_cpu, then allocatable_memory_mb, then node_key ascending",
        f"READY+schedulable pool size (after devnest_node_provider filter): {len(pool)}",
        f"pool nodes satisfying cpu>={req.requested_cpu} and memory_mb>={req.requested_memory_mb}: {len(ranked)}",
    ]
    if ranked:
        first = ranked[0]
        lines.append(f"rank-1 after policy: node_key={first.node_key!r}")
    if not can_fit_workspace(chosen, req):
        lines.append("warning: chosen node does not satisfy request per can_fit_workspace (unexpected)")
    return "\n".join(lines)
