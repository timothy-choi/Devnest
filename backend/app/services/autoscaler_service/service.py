"""
V1 autoscaler — fleet-level capacity (EC2).

- **Scale-up:** optional hook when placement finds no schedulable node (worker path).
- **Scale-down:** internal/admin reclaim of one idle EC2 node (never the last READY EC2 node).

TODO: provisioning jobs / SQS, cooldown windows, predictive signals, per-tenant budgets, ASG integration.
"""

from __future__ import annotations

import logging
import uuid
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
    sync_node_state,
    terminate_ec2_node,
)
from app.services.infrastructure_service.models import Ec2ProvisionRequest, build_default_amazon_linux_2023_user_data
from app.services.placement_service.capacity import max_effective_free_resources_across_schedulable
from app.services.placement_service.capacity import (
    count_active_workloads_on_node_key,
    total_reserved_disk_mb_on_node_key,
    total_reserved_on_node_key,
)
from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeProviderType,
    ExecutionNodeStatus,
)
from app.services.placement_service.node_placement import schedulable_placement_predicates
from app.services.workspace_service.models import Workspace, WorkspaceJob, WorkspaceRuntime
from app.services.workspace_service.models.enums import WorkspaceJobStatus, WorkspaceJobType, WorkspaceStatus

from .models import FleetAutoscalerDecision, FleetCapacitySnapshot, ScaleDownEvaluation, ScaleUpEvaluation

logger = logging.getLogger(__name__)

_PENDING_PLACEMENT_DEMAND_JOB_TYPES = frozenset(
    {
        WorkspaceJobType.CREATE.value,
        WorkspaceJobType.START.value,
        WorkspaceJobType.RESTART.value,
        WorkspaceJobType.UPDATE.value,
        WorkspaceJobType.SNAPSHOT_RESTORE.value,
        WorkspaceJobType.REPO_IMPORT.value,
    },
)

_ACTIVE_EC2_NODE_STATUSES = frozenset(
    {
        ExecutionNodeStatus.PROVISIONING.value,
        ExecutionNodeStatus.READY.value,
        ExecutionNodeStatus.NOT_READY.value,
        ExecutionNodeStatus.DRAINING.value,
        ExecutionNodeStatus.TERMINATING.value,
        ExecutionNodeStatus.ERROR.value,
    },
)


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


def _count_queued_jobs(session: Session, *, job_types: frozenset[str] | None = None) -> int:
    preds = [WorkspaceJob.status == WorkspaceJobStatus.QUEUED.value]
    if job_types is not None:
        preds.append(WorkspaceJob.job_type.in_(sorted(job_types)))
    stmt = select(func.count()).select_from(WorkspaceJob).where(and_(*preds))
    raw = session.exec(stmt).one()
    return int(raw[0] if isinstance(raw, tuple) else raw)


def _seconds_since(ts: datetime | None) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()


def _latest_ec2_created_at(nodes: list[ExecutionNode]) -> datetime | None:
    vals = [
        n.created_at
        for n in nodes
        if n.provider_type == ExecutionNodeProviderType.EC2.value
        and n.status in (ExecutionNodeStatus.PROVISIONING.value, ExecutionNodeStatus.READY.value)
    ]
    return max(vals) if vals else None


def _latest_ec2_scale_in_at(nodes: list[ExecutionNode]) -> datetime | None:
    vals = [
        n.updated_at
        for n in nodes
        if n.provider_type == ExecutionNodeProviderType.EC2.value
        and n.status
        in (
            ExecutionNodeStatus.DRAINING.value,
            ExecutionNodeStatus.TERMINATING.value,
            ExecutionNodeStatus.TERMINATED.value,
        )
    ]
    return max(vals) if vals else None


def _config_int(settings: object, name: str, default: int) -> int:
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def _config_bool(settings: object, name: str, default: bool) -> bool:
    raw = getattr(settings, name, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(raw)


def ec2_autoscaler_provisioning_config_errors(settings: object | None = None) -> list[str]:
    """Return all missing/invalid EC2 settings that would block autoscaler scale-out."""
    s = settings or get_settings()
    errors: list[str] = []
    if not (getattr(s, "aws_region", "") or "").strip():
        errors.append("AWS_REGION is required for EC2 autoscaler provisioning")
    if not (getattr(s, "devnest_ec2_ami_id", "") or "").strip():
        errors.append("DEVNEST_EC2_AMI_ID is required")
    if not (getattr(s, "devnest_ec2_instance_type", "") or "").strip():
        errors.append("DEVNEST_EC2_INSTANCE_TYPE is required")
    if not (getattr(s, "devnest_ec2_subnet_id", "") or "").strip():
        errors.append("DEVNEST_EC2_SUBNET_ID is required")
    raw_sg = (getattr(s, "devnest_ec2_security_group_ids", "") or "").strip()
    if not [x.strip() for x in raw_sg.split(",") if x.strip()]:
        errors.append("DEVNEST_EC2_SECURITY_GROUP_IDS must contain at least one security group id")

    mode = (getattr(s, "devnest_ec2_default_execution_mode", "ssm_docker") or "ssm_docker").strip().lower()
    if mode == "ssm_docker" and not (getattr(s, "devnest_ec2_instance_profile", "") or "").strip():
        errors.append("DEVNEST_EC2_INSTANCE_PROFILE is required when DEVNEST_EC2_DEFAULT_EXECUTION_MODE=ssm_docker")
    if mode == "ssh_docker" and not (getattr(s, "devnest_ec2_key_name", "") or "").strip():
        errors.append("DEVNEST_EC2_KEY_NAME is required when DEVNEST_EC2_DEFAULT_EXECUTION_MODE=ssh_docker")

    has_custom_user_data = bool(
        (getattr(s, "devnest_ec2_user_data", "") or "").strip()
        or (getattr(s, "devnest_ec2_user_data_b64", "") or "").strip()
    )
    internal_base = (
        (getattr(s, "devnest_ec2_heartbeat_internal_api_base_url", "") or "").strip()
        or (getattr(s, "internal_api_base_url", "") or "").strip()
    )
    internal_key = (
        (getattr(s, "internal_api_key_infrastructure", "") or "").strip()
        or (getattr(s, "internal_api_key", "") or "").strip()
    )
    has_generated_bootstrap_config = bool(internal_base and internal_key)
    if (
        not has_custom_user_data
        and not has_generated_bootstrap_config
        and not _config_bool(s, "devnest_ec2_bootstrap_prebaked", False)
    ):
        errors.append(
            "bootstrap config is required: set DEVNEST_EC2_USER_DATA_B64/DEVNEST_EC2_USER_DATA, "
            "or set DEVNEST_EC2_HEARTBEAT_INTERNAL_API_BASE_URL (or INTERNAL_API_BASE_URL) plus "
            "INTERNAL_API_KEY_INFRASTRUCTURE (or INTERNAL_API_KEY) so DevNest can generate Amazon Linux 2023 "
            "user-data, or set DEVNEST_EC2_BOOTSTRAP_PREBAKED=true for an AMI that starts Docker and heartbeat",
        )

    try:
        Ec2ProvisionRequest.from_settings(s).validate()
    except Exception as e:
        msg = str(e)
        if msg and all(msg not in existing for existing in errors):
            errors.append(msg)
    return errors


def _autoscaler_node_key() -> str:
    return f"ec2-autoscale-{uuid.uuid4().hex[:12]}"


def _workspace_projects_base_for_ec2(settings: object) -> str:
    return (
        (getattr(settings, "devnest_ec2_workspace_projects_base", "") or "").strip()
        or (getattr(settings, "workspace_projects_base", "") or "").strip()
        or "/var/lib/devnest/workspace-projects"
    )


def _ec2_heartbeat_internal_api_base_url(settings: object) -> str:
    return (
        (getattr(settings, "devnest_ec2_heartbeat_internal_api_base_url", "") or "").strip()
        or (getattr(settings, "internal_api_base_url", "") or "").strip()
    )


def _ec2_heartbeat_internal_api_key(settings: object) -> str:
    return (
        (getattr(settings, "internal_api_key_infrastructure", "") or "").strip()
        or (getattr(settings, "internal_api_key", "") or "").strip()
    )


def _build_autoscaler_ec2_provision_request(settings: object) -> Ec2ProvisionRequest:
    """Build one EC2 request with a preassigned node key and default AL2023 bootstrap when needed."""
    req = Ec2ProvisionRequest.from_settings(settings)
    req.node_key = _autoscaler_node_key()
    req.name_tag = req.node_key
    has_custom_user_data = bool((req.user_data or "").strip())
    user_data_source = "none"
    if has_custom_user_data:
        req.user_data = (
            (req.user_data or "")
            .replace("{{NODE_KEY}}", req.node_key)
            .replace("{{DEVNEST_NODE_KEY}}", req.node_key)
        )
        user_data_source = "custom"
    elif not _config_bool(settings, "devnest_ec2_bootstrap_prebaked", False):
        req.user_data = build_default_amazon_linux_2023_user_data(
            node_key=req.node_key,
            internal_api_base_url=_ec2_heartbeat_internal_api_base_url(settings),
            internal_api_key=_ec2_heartbeat_internal_api_key(settings),
            workspace_projects_base=_workspace_projects_base_for_ec2(settings),
            heartbeat_interval_seconds=_config_int(settings, "devnest_node_heartbeat_interval_seconds", 30),
        )
        user_data_source = "generated_amazon_linux_2023"
    else:
        user_data_source = "prebaked_ami"
    logger.info(
        "autoscaler_ec2_user_data_prepared",
        extra={
            "node_key": req.node_key,
            "user_data_source": user_data_source,
            "user_data_present": bool((req.user_data or "").strip()),
            "user_data_bytes": len((req.user_data or "").encode("utf-8")),
        },
    )
    return req


def build_fleet_capacity_snapshot(session: Session) -> FleetCapacitySnapshot:
    """Read-only fleet capacity rollup for Phase 1 evaluate-only autoscaler decisions."""
    nodes = list(session.exec(select(ExecutionNode)).all())
    ready_nodes = list(
        session.exec(select(ExecutionNode).where(and_(*schedulable_placement_predicates()))).all(),
    )
    total_nodes = len(nodes)
    ec2_nodes_active = sum(
        1
        for n in nodes
        if n.provider_type == ExecutionNodeProviderType.EC2.value and (n.status or "") in _ACTIVE_EC2_NODE_STATUSES
    )
    provisioning_nodes = sum(
        1
        for n in nodes
        if n.provider_type == ExecutionNodeProviderType.EC2.value
        and n.status == ExecutionNodeStatus.PROVISIONING.value
    )
    draining_nodes = sum(
        1
        for n in nodes
        if n.provider_type == ExecutionNodeProviderType.EC2.value and n.status == ExecutionNodeStatus.DRAINING.value
    )

    active_slots = 0
    free_slots = 0
    total_cpu = 0.0
    free_cpu = 0.0
    total_mem = 0
    free_mem = 0
    total_disk = 0
    free_disk = 0
    for node in ready_nodes:
        key = (node.node_key or "").strip()
        if not key:
            continue
        used_cpu, used_mem = total_reserved_on_node_key(session, key)
        used_disk = total_reserved_disk_mb_on_node_key(session, key)
        slots = count_active_workloads_on_node_key(session, key)
        max_slots = max(0, int(node.max_workspaces or 0))
        alloc_cpu = max(0.0, float(node.allocatable_cpu or 0.0))
        alloc_mem = max(0, int(node.allocatable_memory_mb or 0))
        alloc_disk = max(0, int(node.allocatable_disk_mb or 0))
        active_slots += slots
        free_slots += max(0, max_slots - slots)
        total_cpu += alloc_cpu
        free_cpu += max(0.0, alloc_cpu - used_cpu)
        total_mem += alloc_mem
        free_mem += max(0, alloc_mem - used_mem)
        total_disk += alloc_disk
        free_disk += max(0, alloc_disk - used_disk)

    return FleetCapacitySnapshot(
        total_nodes=total_nodes,
        ec2_nodes_active=ec2_nodes_active,
        ready_schedulable_nodes=len(ready_nodes),
        ready_schedulable_ec2_nodes=sum(
            1 for n in ready_nodes if n.provider_type == ExecutionNodeProviderType.EC2.value
        ),
        provisioning_nodes=provisioning_nodes,
        draining_nodes=draining_nodes,
        active_slots=active_slots,
        free_slots=free_slots,
        pending_workspace_jobs=_count_queued_jobs(session),
        pending_placement_jobs=_count_queued_jobs(session, job_types=_PENDING_PLACEMENT_DEMAND_JOB_TYPES),
        total_allocatable_cpu=round(total_cpu, 4),
        free_cpu=round(free_cpu, 4),
        total_allocatable_memory_mb=total_mem,
        free_memory_mb=free_mem,
        total_allocatable_disk_mb=total_disk,
        free_disk_mb=free_disk,
        idle_ec2_node_count=_count_idle_ec2_nodes(session),
    )


def evaluate_fleet_autoscaler_tick(session: Session) -> FleetAutoscalerDecision:
    """
    Autoscaler controller evaluation.

    This function is intentionally read-only: it does not call EC2, drain, terminate,
    register, or update ``execution_node`` rows. Phase 2 scale-out uses the returned decision
    in :func:`run_scale_out_tick`.
    """
    settings = get_settings()
    enabled = _config_bool(settings, "devnest_autoscaler_enabled", False)
    evaluate_only = _config_bool(settings, "devnest_autoscaler_evaluate_only", True)
    min_nodes = _config_int(settings, "devnest_autoscaler_min_nodes", 1)
    max_nodes = _config_int(settings, "devnest_autoscaler_max_nodes", 10)
    min_idle_slots = _config_int(settings, "devnest_autoscaler_min_idle_slots", 1)
    max_concurrent = _config_int(settings, "devnest_autoscaler_max_concurrent_provisioning", 3)
    out_cooldown = _config_int(settings, "devnest_autoscaler_scale_out_cooldown_seconds", 300)
    in_cooldown = _config_int(settings, "devnest_autoscaler_scale_in_cooldown_seconds", 900)

    cap = build_fleet_capacity_snapshot(session)
    nodes = list(session.exec(select(ExecutionNode)).all())

    pending = int(cap.pending_placement_jobs)
    idle_after_pending = int(cap.free_slots) - pending
    provider_mode = (getattr(settings, "devnest_node_provider", "all") or "all").strip().lower()
    ec2_allowed = provider_mode in ("all", "ec2")
    scale_out_recommended = (
        cap.ready_schedulable_nodes == 0
        or pending > int(cap.free_slots)
        or idle_after_pending < min_idle_slots
    )
    scale_in_recommended = False

    reasons: list[str] = []
    suppressed_by_config = False
    suppressed_by_cap = False
    suppressed_by_cooldown = False

    if scale_out_recommended:
        reasons.append(
            "scale-out recommended: pending placement demand plus idle-slot buffer exceeds ready capacity",
        )
        if not enabled:
            suppressed_by_config = True
            reasons.append("suppressed by config: DEVNEST_AUTOSCALER_ENABLED=false")
        if evaluate_only:
            suppressed_by_config = True
            reasons.append("suppressed by config: DEVNEST_AUTOSCALER_EVALUATE_ONLY=true")
        if not ec2_allowed:
            suppressed_by_config = True
            reasons.append(f"suppressed by config: devnest_node_provider={provider_mode!r} does not allow EC2")
        if enabled and not evaluate_only and ec2_allowed:
            config_errors = ec2_autoscaler_provisioning_config_errors(settings)
            if config_errors:
                suppressed_by_config = True
                reasons.append("suppressed by config: EC2 provision request invalid: " + "; ".join(config_errors))
        if cap.ec2_nodes_active >= max_nodes:
            suppressed_by_cap = True
            reasons.append(f"suppressed by cap: active EC2 nodes {cap.ec2_nodes_active} >= max_nodes {max_nodes}")
        if cap.provisioning_nodes >= max_concurrent:
            suppressed_by_cap = True
            reasons.append(
                f"suppressed by cap: provisioning nodes {cap.provisioning_nodes} >= "
                f"max_concurrent_provisioning {max_concurrent}",
            )
        age = _seconds_since(_latest_ec2_created_at(nodes))
        if age is not None and age >= 0 and age < out_cooldown:
            suppressed_by_cooldown = True
            reasons.append(
                f"suppressed by cooldown: last EC2 provision-like event {int(age)}s ago "
                f"< scale_out_cooldown_seconds {out_cooldown}",
            )
    elif scale_in_recommended:
        reasons.append("scale-in recommended: idle EC2 capacity exceeds configured floor and queue is empty")
        if not enabled:
            suppressed_by_config = True
            reasons.append("suppressed by config: DEVNEST_AUTOSCALER_ENABLED=false")
        if evaluate_only:
            suppressed_by_config = True
            reasons.append("suppressed by config: DEVNEST_AUTOSCALER_EVALUATE_ONLY=true")
        if cap.ec2_nodes_active <= min_nodes:
            suppressed_by_cap = True
            reasons.append(f"suppressed by cap: active EC2 nodes {cap.ec2_nodes_active} <= min_nodes {min_nodes}")
        age = _seconds_since(_latest_ec2_scale_in_at(nodes))
        if age is not None and age >= 0 and age < in_cooldown:
            suppressed_by_cooldown = True
            reasons.append(
                f"suppressed by cooldown: last EC2 scale-in-like event {int(age)}s ago "
                f"< scale_in_cooldown_seconds {in_cooldown}",
            )
    else:
        reasons.append("no action: ready capacity, pending demand, idle buffer, and node floors are within policy")

    if suppressed_by_config:
        action = "suppressed_by_config"
    elif suppressed_by_cap:
        action = "suppressed_by_cap"
    elif suppressed_by_cooldown:
        action = "suppressed_by_cooldown"
    elif scale_out_recommended:
        action = "scale_out_recommended"
    elif scale_in_recommended:
        action = "scale_in_recommended"
    else:
        action = "no_action"

    decision = FleetAutoscalerDecision(
        action=action,
        scale_out_recommended=scale_out_recommended,
        scale_in_recommended=scale_in_recommended,
        no_action=action == "no_action",
        suppressed_by_cooldown=suppressed_by_cooldown,
        suppressed_by_cap=suppressed_by_cap,
        suppressed_by_config=suppressed_by_config,
        reasons=reasons,
        capacity=cap,
        min_nodes=min_nodes,
        max_nodes=max_nodes,
        min_idle_slots=min_idle_slots,
        max_concurrent_provisioning=max_concurrent,
        scale_out_cooldown_seconds=out_cooldown,
        scale_in_cooldown_seconds=in_cooldown,
        evaluate_only=evaluate_only,
        enabled=enabled,
    )
    log_event(
        logger,
        LogEvent.AUTOSCALER_EVALUATE_ONLY_DECISION,
        action=decision.action,
        scale_out_recommended=decision.scale_out_recommended,
        scale_in_recommended=decision.scale_in_recommended,
        suppressed_by_config=decision.suppressed_by_config,
        suppressed_by_cap=decision.suppressed_by_cap,
        suppressed_by_cooldown=decision.suppressed_by_cooldown,
        ready_schedulable_nodes=cap.ready_schedulable_nodes,
        ready_schedulable_ec2_nodes=cap.ready_schedulable_ec2_nodes,
        provisioning_nodes=cap.provisioning_nodes,
        draining_nodes=cap.draining_nodes,
        active_slots=cap.active_slots,
        free_slots=cap.free_slots,
        pending_workspace_jobs=cap.pending_workspace_jobs,
        pending_placement_jobs=cap.pending_placement_jobs,
        idle_ec2_node_count=cap.idle_ec2_node_count,
        reasons=" | ".join(reasons)[:2000],
    )
    return decision


def _sync_provisioning_ec2_nodes(session: Session) -> None:
    """Best-effort readiness reconciliation for nodes already launched by autoscaling/lifecycle."""
    rows = list(
        session.exec(
            select(ExecutionNode)
            .where(
                and_(
                    ExecutionNode.provider_type == ExecutionNodeProviderType.EC2.value,
                    ExecutionNode.status.in_(
                        [
                            ExecutionNodeStatus.PROVISIONING.value,
                            ExecutionNodeStatus.NOT_READY.value,
                        ],
                    ),
                ),
            )
            .order_by(ExecutionNode.node_key.asc()),
        ).all(),
    )
    for row in rows:
        try:
            sync_node_state(session, node_key=row.node_key)
            session.flush()
        except Exception as e:
            logger.warning(
                "autoscaler_provisioning_node_sync_failed",
                extra={"node_key": row.node_key, "error": str(e)[:1000]},
            )


def provision_one_from_fleet_decision(session: Session, decision: FleetAutoscalerDecision) -> ExecutionNode | None:
    """Provision at most one EC2 node when the Phase 2 scale-out decision permits it."""
    if decision.action != "scale_out_recommended":
        log_event(
            logger,
            LogEvent.AUTOSCALER_SCALE_UP_SUPPRESSED,
            detail="scale-out tick did not provision because decision was not scale_out_recommended",
            autoscaler_action=decision.action,
            reasons=" | ".join(decision.reasons)[:2000],
        )
        return None
    req = _build_autoscaler_ec2_provision_request(get_settings())
    node = provision_ec2_node(session, request=req, wait_until_running=True)
    session.flush()
    record_autoscaler_scale_up()
    log_event(
        logger,
        LogEvent.AUTOSCALER_SCALE_UP_TRIGGERED,
        node_key=node.node_key,
        instance_id=(node.provider_instance_id or "").strip() or None,
        provisioning_in_flight_before=decision.capacity.provisioning_nodes,
        autoscaler_action=decision.action,
    )
    logger.info(
        "autoscaler_scale_out_provisioned_one_ec2_node",
        extra={
            "node_key": node.node_key,
            "instance_id": (node.provider_instance_id or "").strip() or None,
            "provisioning_in_flight_before": decision.capacity.provisioning_nodes,
        },
    )
    return node


def run_scale_out_tick(session: Session) -> tuple[FleetAutoscalerDecision, ExecutionNode | None]:
    """
    Phase 2 autoscaler tick: reconcile provisioning readiness, then provision at most one EC2 node.

    No scale-in is performed here.
    """
    _sync_provisioning_ec2_nodes(session)
    decision = evaluate_fleet_autoscaler_tick(session)
    node = provision_one_from_fleet_decision(session, decision)
    return decision, node


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
    decision = evaluate_fleet_autoscaler_tick(session)
    if decision.action != "scale_out_recommended":
        logger.info(
            "autoscaler_skip_provision",
            extra={
                "reason": " | ".join(decision.reasons)[:1000],
                "autoscaler_action": decision.action,
                "provisioning_in_flight": decision.capacity.provisioning_nodes,
            },
        )
        return None
    try:
        node = provision_one_from_fleet_decision(session, decision)
        if node is not None:
            logger.info(
                "autoscaler_provisioned_after_no_schedulable_capacity",
                extra={
                    "node_key": node.node_key,
                    "instance_id": (node.provider_instance_id or "").strip() or None,
                    "provisioning_in_flight_before": decision.capacity.provisioning_nodes,
                },
            )
        return node
    except Exception as e:
        logger.warning(
            "autoscaler_provision_failed",
            extra={"error": str(e), "provisioning_in_flight_before": decision.capacity.provisioning_nodes},
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
