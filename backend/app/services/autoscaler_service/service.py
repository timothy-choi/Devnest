"""
V1 autoscaler — fleet-level capacity (EC2).

- **Scale-up:** optional hook when placement finds no schedulable node (worker path).
- **Scale-down:** internal/admin reclaim of one idle EC2 node (never the last READY EC2 node).

TODO: provisioning jobs / SQS, cooldown windows, predictive signals, per-tenant budgets, ASG integration.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import and_, func
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.libs.observability.log_events import LogEvent, log_event
from app.libs.observability.metrics import record_autoscaler_scale_down, record_autoscaler_scale_up
from app.services.infrastructure_service.errors import Ec2ProvisionConfigurationError
from app.services.infrastructure_service.lifecycle import (
    mark_node_draining,
    provision_ec2_node,
    terminate_ec2_node,
)
from app.services.infrastructure_service.models import Ec2ProvisionRequest
from app.services.placement_service.capacity import max_effective_free_resources_across_schedulable
from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeProviderType,
    ExecutionNodeStatus,
)
from app.services.placement_service.node_placement import schedulable_placement_predicates
from app.services.workspace_service.models import Workspace, WorkspaceRuntime
from app.services.workspace_service.models.enums import WorkspaceStatus

from .models import ScaleDownEvaluation, ScaleUpEvaluation

logger = logging.getLogger(__name__)


def _provider_allows_ec2_autoscale() -> bool:
    mode = (get_settings().devnest_node_provider or "all").strip().lower()
    return mode in ("all", "ec2")


def count_ec2_provisioning_nodes(session: Session) -> int:
    stmt = (
        select(func.count())
        .select_from(ExecutionNode)
        .where(
            and_(
                ExecutionNode.provider_type == ExecutionNodeProviderType.EC2.value,
                ExecutionNode.status == ExecutionNodeStatus.PROVISIONING.value,
            ),
        )
    )
    raw = session.exec(stmt).one()
    return int(raw[0] if isinstance(raw, tuple) else raw)


def _min_ready_ec2_before_reclaim() -> int:
    """Effective floor for last-node safety (never below 2, even if settings are mocked or stale)."""
    return max(2, int(get_settings().devnest_autoscaler_min_ec2_nodes_before_reclaim))


def _workload_counts_by_node_keys(session: Session, node_keys: list[str]) -> dict[str, int]:
    """
    Count non-deleted workspaces pinned to each ``node_key`` via ``WorkspaceRuntime.node_id``.

    Single grouped query for all keys (avoids N+1 in scale-down evaluation).
    """
    keys = sorted({(k or "").strip() for k in node_keys if k and str(k).strip()})
    if not keys:
        return {}
    stmt = (
        select(WorkspaceRuntime.node_id, func.count())
        .select_from(WorkspaceRuntime)
        .join(Workspace, WorkspaceRuntime.workspace_id == Workspace.workspace_id)
        .where(
            WorkspaceRuntime.node_id.in_(keys),
            Workspace.status != WorkspaceStatus.DELETED.value,
        )
        .group_by(WorkspaceRuntime.node_id)
    )
    out: dict[str, int] = {k: 0 for k in keys}
    for row in session.exec(stmt).all():
        nid, cnt = row[0], row[1]
        if nid is None:
            continue
        sk = str(nid).strip()
        if sk in out:
            out[sk] = int(cnt)
    return out


def _count_idle_ec2_nodes(session: Session) -> int:
    """Count READY+schedulable EC2 nodes that have zero active workload placements.

    Uses the same placement pool predicates as scheduling (including
    ``DEVNEST_ENABLE_MULTI_NODE_SCHEDULING`` / primary-node gate when disabled).

    Used for cost-aware scale-up suppression: if idle nodes already exist there is no
    reason to provision more capacity.

    **Cohort note:** uses ``_workload_counts_by_node_keys`` which excludes only DELETED
    workspaces (STOPPED workspaces count as pinning the node).  This is intentionally more
    conservative than the scheduler's ``count_active_workloads_on_node_key`` (which also
    excludes STOPPED and ERROR): a node with only STOPPED workspaces is NOT considered idle
    for scale-up suppression because those workspaces may restart at any moment.  If we
    considered it idle and suppressed scale-up, a burst restart could immediately exhaust
    effective capacity on that node, requiring a new provision cycle anyway.
    """
    stmt = (
        select(ExecutionNode)
        .where(
            and_(
                *schedulable_placement_predicates(),
                ExecutionNode.provider_type == ExecutionNodeProviderType.EC2.value,
            ),
        )
    )
    nodes = list(session.exec(stmt).all())
    if not nodes:
        return 0
    keys = [n.node_key for n in nodes]
    counts = _workload_counts_by_node_keys(session, keys)
    return sum(1 for n in nodes if counts.get((n.node_key or "").strip(), 0) == 0)


def count_ec2_ready_schedulable(session: Session) -> int:
    """Count EC2 rows in the **placement pool** (same predicates as workspace scheduling)."""
    stmt = (
        select(func.count())
        .select_from(ExecutionNode)
        .where(
            and_(
                *schedulable_placement_predicates(),
                ExecutionNode.provider_type == ExecutionNodeProviderType.EC2.value,
            ),
        )
    )
    raw = session.exec(stmt).one()
    return int(raw[0] if isinstance(raw, tuple) else raw)


def workload_count_on_node(session: Session, node_key: str) -> int:
    """
    Count non-deleted workspaces whose runtime row is pinned to ``node_key``.

    Conservative: any non-DELETED workspace with this ``WorkspaceRuntime.node_id`` counts as active placement.
    """
    key = (node_key or "").strip()
    if not key:
        return 0
    return int(_workload_counts_by_node_keys(session, [key]).get(key, 0))


def evaluate_scale_up(
    session: Session,
    *,
    insufficient_capacity: bool,
) -> ScaleUpEvaluation:
    """
    Decide whether to add one EC2 node.

    Requires ``insufficient_capacity=True`` from caller (e.g. placement failure). Honors
    ``devnest_autoscaler_max_concurrent_provisioning`` and EC2 settings completeness.
    """
    settings = get_settings()
    in_flight = count_ec2_provisioning_nodes(session)
    if not settings.devnest_autoscaler_enabled:
        return ScaleUpEvaluation(
            should_provision=False,
            reason="autoscaler disabled (devnest_autoscaler_enabled=false)",
            provisioning_in_flight=in_flight,
        )
    if not insufficient_capacity:
        return ScaleUpEvaluation(
            should_provision=False,
            reason="capacity not marked insufficient",
            provisioning_in_flight=in_flight,
        )
    ec2_allowed = _provider_allows_ec2_autoscale()
    if ec2_allowed:
        try:
            ec2_preds = [
                *schedulable_placement_predicates(),
                ExecutionNode.provider_type == ExecutionNodeProviderType.EC2.value,
            ]
            max_cpu_ec2, max_mem_ec2 = max_effective_free_resources_across_schedulable(
                session,
                base_predicates=ec2_preds,
            )
        except Exception:
            max_cpu_ec2, max_mem_ec2 = None, None
        logger.info(
            "autoscaler_evaluate_insufficient_capacity_context",
            extra={
                "max_effective_free_cpu_ec2_ready_pool": max_cpu_ec2,
                "max_effective_free_memory_mb_ec2_ready_pool": max_mem_ec2,
                "provisioning_in_flight": in_flight,
            },
        )
    if not ec2_allowed:
        return ScaleUpEvaluation(
            should_provision=False,
            reason="devnest_node_provider is local-only; EC2 autoscale skipped",
            provisioning_in_flight=in_flight,
        )
    # --- Cost-aware suppression: prefer reusing idle nodes before provisioning new ones ---
    n_idle = _count_idle_ec2_nodes(session)
    if n_idle > 0:
        reason = (
            f"scale-up suppressed: {n_idle} idle EC2 node(s) with zero active workloads exist; "
            "prefer reusing existing capacity before provisioning"
        )
        log_event(
            logger,
            LogEvent.AUTOSCALER_SCALE_UP_SUPPRESSED,
            idle_ec2_node_count=n_idle,
            provisioning_in_flight=in_flight,
            detail=reason,
        )
        return ScaleUpEvaluation(
            should_provision=False,
            reason=reason,
            provisioning_in_flight=in_flight,
            idle_ec2_node_count=n_idle,
        )
    if in_flight >= int(settings.devnest_autoscaler_max_concurrent_provisioning):
        return ScaleUpEvaluation(
            should_provision=False,
            reason=(
                f"at concurrent provisioning cap ({in_flight} >= "
                f"{settings.devnest_autoscaler_max_concurrent_provisioning})"
            ),
            provisioning_in_flight=in_flight,
        )
    req = Ec2ProvisionRequest.from_settings(settings)
    try:
        req.validate()
    except Ec2ProvisionConfigurationError as e:
        return ScaleUpEvaluation(
            should_provision=False,
            reason=f"EC2 provision request invalid: {e}",
            provisioning_in_flight=in_flight,
        )
    return ScaleUpEvaluation(
        should_provision=True,
        reason="insufficient capacity and under concurrent provisioning cap with valid EC2 defaults",
        provisioning_in_flight=in_flight,
    )


def provision_capacity_if_needed(session: Session, evaluation: ScaleUpEvaluation) -> ExecutionNode | None:
    """Launch one EC2 instance when :class:`ScaleUpEvaluation` allows. Caller commits."""
    if not evaluation.should_provision:
        return None
    node = provision_ec2_node(session, request=None, wait_until_running=True)
    session.flush()
    record_autoscaler_scale_up()
    log_event(
        logger,
        LogEvent.AUTOSCALER_SCALE_UP_TRIGGERED,
        node_key=node.node_key,
        instance_id=(node.provider_instance_id or "").strip() or None,
        provisioning_in_flight_before=evaluation.provisioning_in_flight,
    )
    logger.info(
        "autoscaler_provisioned_ec2_node",
        extra={
            "node_key": node.node_key,
            "instance_id": (node.provider_instance_id or "").strip() or None,
            "provisioning_in_flight_before": evaluation.provisioning_in_flight,
        },
    )
    return node


def maybe_provision_on_no_schedulable_capacity(session: Session) -> ExecutionNode | None:
    """
    Best-effort scale-up when placement could not schedule (``NoSchedulableNodeError``).

    Controlled by ``devnest_autoscaler_enabled`` and
    ``devnest_autoscaler_provision_on_no_capacity``. Does not block job failure if provisioning fails.
    """
    settings = get_settings()
    if not settings.devnest_autoscaler_enabled or not settings.devnest_autoscaler_provision_on_no_capacity:
        return None
    ev = evaluate_scale_up(session, insufficient_capacity=True)
    if not ev.should_provision:
        logger.info(
            "autoscaler_skip_provision",
            extra={"reason": ev.reason, "provisioning_in_flight": ev.provisioning_in_flight},
        )
        return None
    try:
        node = provision_capacity_if_needed(session, ev)
        if node is not None:
            logger.info(
                "autoscaler_provisioned_after_no_schedulable_capacity",
                extra={
                    "node_key": node.node_key,
                    "instance_id": (node.provider_instance_id or "").strip() or None,
                    "provisioning_in_flight_before": ev.provisioning_in_flight,
                },
            )
        return node
    except Exception as e:
        logger.warning(
            "autoscaler_provision_failed",
            extra={"error": str(e), "provisioning_in_flight_before": ev.provisioning_in_flight},
        )
        return None


def evaluate_scale_down(session: Session) -> ScaleDownEvaluation:
    """
    Find whether an idle EC2 node could be reclaimed.

    Never selects the last READY+schedulable EC2 node. Local nodes are never considered.
    """
    n_ready = count_ec2_ready_schedulable(session)
    min_ready_required = _min_ready_ec2_before_reclaim()
    if n_ready < min_ready_required:
        reason = (
            f"READY+schedulable EC2 count {n_ready} below minimum {min_ready_required} "
            f"(devnest_autoscaler_min_ec2_nodes_before_reclaim; last-node safety)"
        )
        log_event(
            logger,
            LogEvent.AUTOSCALER_SCALE_DOWN_SUPPRESSED,
            idle_ec2_ready_nodes=n_ready,
            min_ready_required=min_ready_required,
            detail=reason,
        )
        return ScaleDownEvaluation(
            node_key=None,
            reason=reason,
            idle_ec2_ready_nodes=n_ready,
        )
    stmt = (
        select(ExecutionNode)
        .where(
            and_(
                ExecutionNode.provider_type == ExecutionNodeProviderType.EC2.value,
                ExecutionNode.status == ExecutionNodeStatus.READY.value,
                ExecutionNode.schedulable == True,  # noqa: E712
            ),
        )
        .order_by(ExecutionNode.node_key.asc())
    )
    rows = list(session.exec(stmt).all())
    keys = [r.node_key for r in rows]
    counts = _workload_counts_by_node_keys(session, keys)
    idle = [r for r in rows if counts.get((r.node_key or "").strip(), 0) == 0]
    if not idle:
        reason = "no idle EC2 nodes (all have workspace_runtime placements for non-deleted workspaces)"
        log_event(
            logger,
            LogEvent.AUTOSCALER_SCALE_DOWN_SUPPRESSED,
            idle_ec2_ready_nodes=n_ready,
            detail=reason,
        )
        return ScaleDownEvaluation(
            node_key=None,
            reason=reason,
            idle_ec2_ready_nodes=n_ready,
        )
    # Cost-aware: reclaim the node with the smallest allocatable capacity first.
    # This preserves larger-capacity nodes for future workloads requiring more resources.
    # Tiebreak by allocatable_memory_mb, then node_key for stability.
    idle.sort(
        key=lambda n: (
            float(n.allocatable_cpu or 0.0),
            int(n.allocatable_memory_mb or 0),
            (n.node_key or ""),
        )
    )
    pick = idle[0]
    return ScaleDownEvaluation(
        node_key=pick.node_key,
        reason=(
            f"idle EC2 node selected for reclaim (smallest allocatable_cpu={pick.allocatable_cpu} "
            f"allocatable_memory_mb={pick.allocatable_memory_mb}; preserves higher-capacity nodes); "
            f"{n_ready} READY+schedulable EC2 node(s) (minimum before reclaim={min_ready_required})"
        ),
        idle_ec2_ready_nodes=n_ready,
    )


def select_node_for_scale_down(session: Session) -> ExecutionNode | None:
    """Return the node row for :func:`evaluate_scale_down` candidate, or ``None``."""
    ev = evaluate_scale_down(session)
    if not ev.node_key:
        return None
    stmt = select(ExecutionNode).where(ExecutionNode.node_key == ev.node_key)
    return session.exec(stmt).first()


def _node_has_recent_activity(
    session: Session,
    node_key: str,
    *,
    window_seconds: int,
) -> bool:
    """Return True when a workspace runtime on ``node_key`` had recent heartbeat activity.

    Uses ``WorkspaceRuntime.last_heartbeat_at`` as a proxy for "recently active". A node is
    considered active if any workspace runtime row pinned to it was heartbeated within the last
    ``window_seconds`` seconds. This prevents draining nodes that still host warm workloads.
    """
    from datetime import timezone  # noqa: PLC0415
    if window_seconds <= 0:
        return False
    cutoff = datetime.now(timezone.utc).timestamp() - window_seconds
    stmt = (
        select(WorkspaceRuntime)
        .where(
            and_(
                WorkspaceRuntime.node_id == node_key,
                WorkspaceRuntime.last_heartbeat_at.isnot(None),
            )
        )
        .limit(10)
    )
    runtimes = list(session.exec(stmt).all())
    for rt in runtimes:
        if rt.last_heartbeat_at is None:
            continue
        hb_ts = rt.last_heartbeat_at.timestamp() if hasattr(rt.last_heartbeat_at, "timestamp") else 0.0
        if hb_ts > cutoff:
            return True
    return False


def _find_draining_node_past_delay(
    session: Session,
    *,
    drain_delay_seconds: int,
) -> ExecutionNode | None:
    """Find a DRAINING EC2 node that has been draining for at least ``drain_delay_seconds``.

    Uses ``ExecutionNode.updated_at`` as the drain-started-at proxy. Returns the first
    candidate sorted by ``node_key`` for determinism.
    """
    from datetime import timezone  # noqa: PLC0415
    if drain_delay_seconds <= 0:
        stmt = (
            select(ExecutionNode)
            .where(
                and_(
                    ExecutionNode.provider_type == ExecutionNodeProviderType.EC2.value,
                    ExecutionNode.status == ExecutionNodeStatus.DRAINING.value,
                )
            )
            .order_by(ExecutionNode.node_key.asc())
            .limit(1)
        )
        return session.exec(stmt).first()

    cutoff = datetime.now(timezone.utc).timestamp() - drain_delay_seconds
    stmt = (
        select(ExecutionNode)
        .where(
            and_(
                ExecutionNode.provider_type == ExecutionNodeProviderType.EC2.value,
                ExecutionNode.status == ExecutionNodeStatus.DRAINING.value,
            )
        )
        .order_by(ExecutionNode.node_key.asc())
    )
    candidates = list(session.exec(stmt).all())
    for node in candidates:
        if node.updated_at is None:
            return node  # unknown drain start; allow termination
        ua_ts = node.updated_at.timestamp() if hasattr(node.updated_at, "timestamp") else 0.0
        if ua_ts <= cutoff:
            return node
    return None


def reclaim_one_idle_ec2_node(
    session: Session,
    *,
    ec2_client: object | None = None,
) -> ExecutionNode | None:
    """
    Two-phase drain + terminate for a safe, non-premature scale-down.

    **Phase 1 — Mark:** Find an idle READY EC2 node, check for recent workspace activity,
    mark it ``DRAINING``. The node will not be immediately terminated.

    **Phase 2 — Terminate:** Find a DRAINING node that has waited at least
    ``DEVNEST_AUTOSCALER_DRAIN_DELAY_SECONDS`` (default 30 s) and terminate it.

    Both phases run on each call; a node marked in Phase 1 will be terminated on the next
    call once the delay elapses.

    **Destructive:** use internal/admin routes only. Caller must ``commit``.
    """
    settings = get_settings()
    drain_delay = int(getattr(settings, "devnest_autoscaler_drain_delay_seconds", 30))
    activity_window = int(getattr(settings, "devnest_autoscaler_recent_activity_window_seconds", 300))

    # Phase 2: terminate a DRAINING node past the delay window.
    out: ExecutionNode | None = None
    draining_candidate = _find_draining_node_past_delay(session, drain_delay_seconds=drain_delay)
    if draining_candidate is not None:
        out = terminate_ec2_node(session, node_key=draining_candidate.node_key, ec2_client=ec2_client)
        record_autoscaler_scale_down()
        log_event(
            logger,
            LogEvent.AUTOSCALER_SCALE_DOWN_TRIGGERED,
            node_key=draining_candidate.node_key,
            instance_id=(draining_candidate.provider_instance_id or "").strip() or None,
            drain_delay_seconds=drain_delay,
        )

    # Phase 1: select a new idle node and mark it DRAINING (for future termination).
    ready_node = select_node_for_scale_down(session)
    if ready_node is not None:
        # Safety: skip nodes with recent workspace heartbeat activity.
        nk = (ready_node.node_key or "").strip()
        if activity_window > 0 and _node_has_recent_activity(session, nk, window_seconds=activity_window):
            logger.info(
                "autoscaler_drain_suppressed_recent_activity",
                extra={"node_key": nk, "activity_window_seconds": activity_window},
            )
        else:
            mark_node_draining(session, node_key=ready_node.node_key)
            logger.info(
                "autoscaler_node_marked_draining",
                extra={
                    "node_key": nk,
                    "drain_delay_seconds": drain_delay,
                    "will_terminate_after": f"{drain_delay}s",
                },
            )

    return out
