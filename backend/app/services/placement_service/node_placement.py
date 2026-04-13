"""V1 node selection for workspace runtime placement (conservative policy)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, func, or_
from sqlmodel import Session, select

from app.libs.common.config import get_settings

from .capacity import (
    active_workload_count_subquery,
    effective_free_cpu_expr,
    effective_free_memory_mb_expr,
    max_effective_free_resources_across_schedulable,
)
from .constants import DEFAULT_WORKSPACE_REQUEST_CPU, DEFAULT_WORKSPACE_REQUEST_MEMORY_MB
from .errors import ExecutionNodeNotFoundError, InvalidPlacementParametersError, NoSchedulableNodeError
from .models import ExecutionNode, ExecutionNodeProviderType, ExecutionNodeStatus


def _provider_type_clause():
    """
    Optional placement pool filter from ``DEVNEST_NODE_PROVIDER`` (``local`` | ``ec2`` | ``all``).

    Default ``all`` does not restrict by provider; local-only and EC2-only clusters can narrow the pool.
    ``local`` also allows ``provider_type=unspecified`` for legacy rows.
    """
    mode = (get_settings().devnest_node_provider or "all").strip().lower()
    if mode == "local":
        # Treat legacy / unset provider label like local for backward compatibility.
        return or_(
            ExecutionNode.provider_type == ExecutionNodeProviderType.LOCAL.value,
            ExecutionNode.provider_type == ExecutionNodeProviderType.UNSPECIFIED.value,
        )
    if mode == "ec2":
        return ExecutionNode.provider_type == ExecutionNodeProviderType.EC2.value
    return None


def _schedulable_base_predicates():
    preds = [
        ExecutionNode.schedulable == True,  # noqa: E712
        ExecutionNode.status == ExecutionNodeStatus.READY.value,
    ]
    p = _provider_type_clause()
    if p is not None:
        preds.append(p)
    return preds


def schedulable_placement_predicates() -> list:
    """Public: SQLAlchemy boolean clauses for READY + schedulable (+ ``devnest_node_provider`` filter)."""
    return list(_schedulable_base_predicates())


def _count_ready_schedulable_nodes(session: Session) -> int:
    stmt = (
        select(func.count())
        .select_from(ExecutionNode)
        .where(and_(*_schedulable_base_predicates()))
    )
    raw = session.exec(stmt).one()
    return int(raw[0] if isinstance(raw, tuple) else raw)


def list_schedulable_nodes(session: Session) -> list[ExecutionNode]:
    """Nodes that pass the READY + schedulable gate (capacity not checked)."""
    stmt = (
        select(ExecutionNode)
        .where(and_(*_schedulable_base_predicates()))
        .order_by(ExecutionNode.node_key.asc())
    )
    return list(session.exec(stmt).all())


def get_node(session: Session, *, node_id: int | None = None, node_key: str | None = None) -> ExecutionNode:
    """Load a single node by database PK (``node_id`` = :class:`ExecutionNode`.id) or ``node_key``."""
    if node_id is not None:
        row = session.get(ExecutionNode, node_id)
        if row is None:
            raise ExecutionNodeNotFoundError(f"execution node id={node_id} not found")
        return row
    if node_key is not None and str(node_key).strip():
        key = str(node_key).strip()
        stmt = select(ExecutionNode).where(ExecutionNode.node_key == key)
        row = session.exec(stmt).first()
        if row is None:
            raise ExecutionNodeNotFoundError(f"execution node key={key!r} not found")
        return row
    raise ExecutionNodeNotFoundError("node_id or node_key is required")


def select_node_for_workspace(
    session: Session,
    *,
    workspace_id: int,
    requested_cpu: float = DEFAULT_WORKSPACE_REQUEST_CPU,
    requested_memory_mb: int = DEFAULT_WORKSPACE_REQUEST_MEMORY_MB,
    for_update: bool = False,
) -> ExecutionNode:
    """
    Choose a node for a workspace bring-up class job.

    Policy: READY + schedulable, enough **effective** free CPU/RAM:
    ``allocatable_*`` minus sums of ``WorkspaceRuntime.reserved_*`` for workloads on that ``node_key``
    (workspace not ``STOPPED`` / ``DELETED`` / ``ERROR``).

    Tie-break: highest effective free CPU, then effective free RAM, then ``node_key`` ascending.

    Keep ordering aligned with :func:`app.services.scheduler_service.policy.rank_candidate_nodes`.

    ``workspace_id`` is accepted for future affinity / anti-affinity; unused in V1.

    When ``DEVNEST_NODE_PROVIDER`` is ``local`` or ``ec2``, only matching ``provider_type`` rows are
    considered (see :func:`_provider_type_clause`).

    TODO: Optional staleness gate on ``last_heartbeat_at`` once node agents report in; keep
    ``NULL`` heartbeats valid for dev/single-node bootstrap.

    Raises:
        InvalidPlacementParametersError: when request sizes are not positive.
        NoSchedulableNodeError: when no node qualifies.
    """
    _ = workspace_id  # reserved for affinity (V2+)
    req_cpu = float(requested_cpu)
    req_mem = int(requested_memory_mb)
    if req_cpu <= 0 or req_mem <= 0:
        raise InvalidPlacementParametersError(
            "placement requires positive requested_cpu and requested_memory_mb "
            f"(got cpu={requested_cpu!r}, memory_mb={requested_memory_mb!r})",
        )

    free_cpu_e = effective_free_cpu_expr()
    free_mem_e = effective_free_memory_mb_expr()
    active_wl_e = active_workload_count_subquery()
    preds = [
        *_schedulable_base_predicates(),
        free_cpu_e >= req_cpu,
        free_mem_e >= req_mem,
    ]
    stmt = (
        select(ExecutionNode)
        .where(and_(*preds))
        .order_by(
            # Primary: most effective free CPU (capacity-first to avoid fragmentation).
            free_cpu_e.desc(),
            # Secondary: most effective free memory.
            free_mem_e.desc(),
            # Tertiary: fewer active workloads (spread / anti-concentration fairness).
            active_wl_e.asc(),
            # Stable tiebreak.
            ExecutionNode.node_key.asc(),
        )
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = session.exec(stmt).first()
    if row is None:
        n_gate = _count_ready_schedulable_nodes(session)
        prov = (get_settings().devnest_node_provider or "all").strip().lower()
        pool_hint = ""
        if prov in ("local", "ec2"):
            pool_hint = f" [placement pool: devnest_node_provider={prov!r}]"
        max_cpu, max_mem = max_effective_free_resources_across_schedulable(
            session,
            base_predicates=list(_schedulable_base_predicates()),
        )
        raise NoSchedulableNodeError(
            "no execution node matches placement policy "
            f"(need status=READY, schedulable=true, effective_free_cpu>={req_cpu}, "
            f"effective_free_memory_mb>={req_mem} after workspace reservations; "
            f"{n_gate} node(s) are READY+schedulable (after provider filter) but none have enough "
            f"effective capacity — check execution_node, workspace_runtime reservations, and bootstrap; "
            f"diagnostic max_effective_free_cpu≈{max_cpu:.4f}, "
            f"max_effective_free_memory_mb≈{max_mem} in that pool)"
            f"{pool_hint}",
        )
    return row


def reserve_node_for_workspace(
    session: Session,
    *,
    workspace_id: int,
    requested_cpu: float = DEFAULT_WORKSPACE_REQUEST_CPU,
    requested_memory_mb: int = DEFAULT_WORKSPACE_REQUEST_MEMORY_MB,
) -> ExecutionNode:
    """
    Same as :func:`select_node_for_workspace` but locks the chosen row (``FOR UPDATE``).

    Serializes concurrent placement on the same node for the duration of the caller's transaction.
    Reservations are persisted on ``WorkspaceRuntime`` when the job succeeds (see worker).
    """
    return select_node_for_workspace(
        session,
        workspace_id=workspace_id,
        requested_cpu=requested_cpu,
        requested_memory_mb=requested_memory_mb,
        for_update=True,
    )


def touch_node_heartbeat(session: Session, node: ExecutionNode) -> None:
    """Optional hook for future node agents; updates ``last_heartbeat_at`` (control-plane writes)."""
    node.last_heartbeat_at = datetime.now(timezone.utc)
    node.updated_at = datetime.now(timezone.utc)
    session.add(node)
