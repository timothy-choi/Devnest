"""V1 node selection for workspace runtime placement (conservative policy)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from .constants import DEFAULT_WORKSPACE_REQUEST_CPU, DEFAULT_WORKSPACE_REQUEST_MEMORY_MB
from .errors import ExecutionNodeNotFoundError, NoSchedulableNodeError
from .models import ExecutionNode, ExecutionNodeStatus


def list_schedulable_nodes(session: Session) -> list[ExecutionNode]:
    """Nodes that pass the READY + schedulable gate (capacity not checked)."""
    stmt = (
        select(ExecutionNode)
        .where(
            ExecutionNode.schedulable == True,  # noqa: E712
            ExecutionNode.status == ExecutionNodeStatus.READY.value,
        )
        .order_by(ExecutionNode.node_key.asc())
    )
    return list(session.exec(stmt).all())


def get_node(session: Session, *, node_id: int | None = None, node_key: str | None = None) -> ExecutionNode:
    """Load a single node by primary key or ``node_key``."""
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

    Policy: READY + schedulable, enough allocatable CPU/RAM (filter-only; no persistent accounting).
    Tie-break: most allocatable CPU, then most RAM, then ``node_key`` ascending (deterministic).

    ``workspace_id`` is accepted for future affinity / anti-affinity; unused in V1.

    TODO: Optional staleness gate on ``last_heartbeat_at`` once node agents report in; keep
    ``NULL`` heartbeats valid for dev/single-node bootstrap.

    Raises:
        NoSchedulableNodeError: when no node qualifies.
    """
    _ = workspace_id  # reserved for affinity (V2+)
    stmt = (
        select(ExecutionNode)
        .where(
            ExecutionNode.schedulable == True,  # noqa: E712
            ExecutionNode.status == ExecutionNodeStatus.READY.value,
            ExecutionNode.allocatable_cpu >= float(requested_cpu),
            ExecutionNode.allocatable_memory_mb >= int(requested_memory_mb),
        )
        .order_by(
            ExecutionNode.allocatable_cpu.desc(),
            ExecutionNode.allocatable_memory_mb.desc(),
            ExecutionNode.node_key.asc(),
        )
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = session.exec(stmt).first()
    if row is None:
        raise NoSchedulableNodeError(
            "no schedulable execution node satisfies capacity policy "
            f"(cpu>={requested_cpu}, memory_mb>={requested_memory_mb})",
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

    V1 does **not** decrement allocatable_* (no cluster usage accounting yet); callers should
    treat this as a serialization point when extending to real reservations.
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
