"""Schedule workspaces onto execution nodes and produce human-readable explanations."""

from __future__ import annotations

import logging

from sqlmodel import Session

from app.libs.common.config import get_settings
from app.services.placement_service.node_heartbeat import execution_node_heartbeat_age_seconds
from app.services.placement_service.constants import (
    DEFAULT_WORKSPACE_REQUEST_CPU,
    DEFAULT_WORKSPACE_REQUEST_DISK_MB,
    DEFAULT_WORKSPACE_REQUEST_MEMORY_MB,
)
from app.services.placement_service.capacity import (
    count_active_workloads_on_node_key,
    total_reserved_disk_mb_on_node_key,
    total_reserved_on_node_key,
)
from app.services.placement_service.errors import InvalidPlacementParametersError, NoSchedulableNodeError
from app.services.placement_service.models import ExecutionNode
from app.libs.observability.log_events import LogEvent, log_event
from app.services.placement_service.node_placement import list_schedulable_nodes, reserve_node_for_workspace

from .models import WorkspaceComputeRequest, WorkspaceScheduleResult
from .policy import can_fit_workspace_effective, rank_candidate_nodes

logger = logging.getLogger(__name__)


def _placement_telemetry() -> dict[str, bool | str]:
    """Structured fields for placement logs (Step 7): gate + human-readable selection reason."""
    mns = bool(get_settings().devnest_enable_multi_node_scheduling)
    gate = not mns
    reason = (
        "primary_only:min_execution_node_id_among_ready_schedulable_after_provider_filter"
        if gate
        else "multi_node:rank_by_effective_free_resources_then_spread_then_node_key_stable_tiebreak"
    )
    return {
        "multi_node_scheduling_enabled": mns,
        "placement_single_node_gate": gate,
        "placement_reason": reason,
    }


def schedule_workspace(
    session: Session,
    *,
    workspace_id: int,
    requested_cpu: float = DEFAULT_WORKSPACE_REQUEST_CPU,
    requested_memory_mb: int = DEFAULT_WORKSPACE_REQUEST_MEMORY_MB,
    requested_disk_mb: int = DEFAULT_WORKSPACE_REQUEST_DISK_MB,
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
            requested_disk_mb=requested_disk_mb,
        )
        log_event(
            logger,
            LogEvent.SCHEDULER_NODE_SELECTED,
            workspace_id=workspace_id,
            execution_node_id=node.id,
            node_key=node.node_key,
            requested_cpu=requested_cpu,
            requested_memory_mb=requested_memory_mb,
            requested_disk_mb=requested_disk_mb,
            target_node_heartbeat_age_seconds=execution_node_heartbeat_age_seconds(node),
            **_placement_telemetry(),
        )
        try:
            explain = explain_placement_decision(
                session,
                chosen=node,
                workspace_id=workspace_id,
                requested_cpu=requested_cpu,
                requested_memory_mb=requested_memory_mb,
                requested_disk_mb=requested_disk_mb,
            )
            digest = (explain or "").replace("\n", " | ").replace("\r", "")[:900]
            if digest:
                log_event(
                    logger,
                    LogEvent.PLACEMENT_DECISION_SUMMARY,
                    workspace_id=workspace_id,
                    execution_node_id=node.id,
                    node_key=node.node_key,
                    placement_summary=digest,
                    target_node_heartbeat_age_seconds=execution_node_heartbeat_age_seconds(node),
                )
        except Exception:
            logger.debug("placement_decision_summary_skipped", exc_info=True)
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
        settings = get_settings()
        log_event(
            logger,
            LogEvent.PLACEMENT_NO_SCHEDULABLE_NODE,
            level=logging.WARNING,
            workspace_id=workspace_id,
            requested_cpu=requested_cpu,
            requested_memory_mb=requested_memory_mb,
            requested_disk_mb=requested_disk_mb,
            **_placement_telemetry(),
            heartbeat_gate_enabled=bool(getattr(settings, "devnest_require_fresh_node_heartbeat", False)),
            node_heartbeat_max_age_seconds=int(getattr(settings, "devnest_node_heartbeat_max_age_seconds", 300) or 300),
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
    requested_disk_mb: int = DEFAULT_WORKSPACE_REQUEST_DISK_MB,
) -> str:
    """
    Summarize why ``chosen`` was selected among READY+schedulable nodes (best-effort; filter-only).

    ``workspace_id`` is accepted for future affinity context; unused in V1.
    """
    _ = workspace_id
    req = WorkspaceComputeRequest(
        requested_cpu=float(requested_cpu),
        requested_memory_mb=int(requested_memory_mb),
        requested_disk_mb=int(requested_disk_mb),
    )
    pool = list_schedulable_nodes(session)
    ranked = rank_candidate_nodes(session, pool, req)
    k = (chosen.node_key or "").strip()
    used_c, used_m = total_reserved_on_node_key(session, k)
    used_d = total_reserved_disk_mb_on_node_key(session, k)
    free_c = max(0.0, float(chosen.allocatable_cpu or 0.0) - used_c)
    free_m = max(0, int(chosen.allocatable_memory_mb or 0) - used_m)
    free_d = max(0, int(chosen.allocatable_disk_mb or 0) - used_d)
    wcount = count_active_workloads_on_node_key(session, k)
    mns = bool(get_settings().devnest_enable_multi_node_scheduling)
    pool_note = (
        "READY+schedulable pool (after devnest_node_provider filter"
        + ("" if mns else "; DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=false → primary node=min(id) only")
        + ")"
    )
    lines: list[str] = [
        f"selected node_key={chosen.node_key!r} allocatable_cpu={chosen.allocatable_cpu} "
        f"allocatable_memory_mb={chosen.allocatable_memory_mb} allocatable_disk_mb={chosen.allocatable_disk_mb} "
        f"max_workspaces={chosen.max_workspaces}",
        f"effective_free_cpu={free_c:.4f} effective_free_memory_mb={free_m} "
        f"effective_free_disk_mb={free_d} active_workload_count={wcount} "
        f"(reservations from workspace_runtime; STOPPED/DELETED/ERROR workspaces excluded)",
        f"sort_policy: capacity-first (effective_free_cpu desc, effective_free_memory_mb desc), "
        f"then active_workload_count asc (spread/anti-concentration), then node_key asc (stable tiebreak)",
        f"{pool_note}: {len(pool)} node(s)",
        f"pool nodes satisfying effective capacity for "
        f"cpu>={req.requested_cpu}, memory_mb>={req.requested_memory_mb}, "
        f"disk_mb>={req.requested_disk_mb}, and workspace slots: {len(ranked)}",
    ]
    if ranked:
        first = ranked[0]
        lines.append(f"rank-1 after policy: node_key={first.node_key!r}")
    if not can_fit_workspace_effective(free_c, free_m, free_d, req):
        lines.append(
            "warning: chosen node does not satisfy request per can_fit_workspace_effective (unexpected)",
        )
    return "\n".join(lines)
