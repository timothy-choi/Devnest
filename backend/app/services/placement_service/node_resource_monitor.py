"""Per-node EC2 host resource checks via SSM (workspace-worker background loop)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from botocore.client import BaseClient
from sqlalchemy import and_
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.libs.observability.log_events import LogEvent, log_event
from app.services.node_execution_service.ssm_send_command import build_ssm_client, send_run_shell_script
from app.services.node_execution_service.errors import SsmExecutionError
from app.services.placement_service.host_resource import (
    HOST_RESOURCE_PROBE_SCRIPT,
    parse_host_resource_ssm_stdout,
)
from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
    ExecutionNodeResourceStatus,
    ExecutionNodeStatus,
)
from app.services.placement_service.node_heartbeat import execution_node_heartbeat_within_max_age

logger = logging.getLogger(__name__)


def _apply_resource_metrics_to_node(
    session: Session,
    node: ExecutionNode,
    metrics: dict[str, int | str | None],
    *,
    docker_note: str | None,
) -> None:
    settings = get_settings()
    min_disk = int(settings.devnest_node_min_free_disk_mb)
    min_mem = int(settings.devnest_node_min_free_memory_mb)
    now = datetime.now(timezone.utc)

    disk_free = metrics.get("disk_free_mb")
    mem_free = metrics.get("memory_free_mb")
    disk_total = metrics.get("disk_total_mb")
    mem_total = metrics.get("memory_total_mb")

    prev_low = (node.resource_status or "").strip().upper() in (
        ExecutionNodeResourceStatus.LOW_DISK.value,
        ExecutionNodeResourceStatus.LOW_MEMORY.value,
    )

    if isinstance(disk_total, int):
        node.disk_total_mb = disk_total
    if isinstance(disk_free, int):
        node.disk_free_mb = disk_free
    if isinstance(mem_total, int):
        node.memory_total_mb = mem_total
    if isinstance(mem_free, int):
        node.memory_free_mb = mem_free
    node.last_resource_check_at = now

    low_disk = isinstance(disk_free, int) and disk_free < min_disk
    low_mem = isinstance(mem_free, int) and mem_free < min_mem

    if low_disk or low_mem:
        warn_parts: list[str] = []
        if low_disk:
            warn_parts.append(f"disk_free_mb={disk_free} < min_free_disk_mb={min_disk}")
        if low_mem:
            warn_parts.append(f"memory_free_mb={mem_free} < min_free_memory_mb={min_mem}")
        node.resource_status = (
            ExecutionNodeResourceStatus.LOW_DISK.value if low_disk else ExecutionNodeResourceStatus.LOW_MEMORY.value
        )
        if low_disk:
            log_event(
                logger,
                LogEvent.NODE_RESOURCE_LOW_DISK,
                node_key=node.node_key,
                instance_id=(node.provider_instance_id or "").strip() or None,
                disk_free_mb=disk_free,
                min_free_disk_mb=min_disk,
            )
        if low_mem:
            log_event(
                logger,
                LogEvent.NODE_RESOURCE_LOW_MEMORY,
                node_key=node.node_key,
                instance_id=(node.provider_instance_id or "").strip() or None,
                memory_free_mb=mem_free,
                min_free_memory_mb=min_mem,
            )
        msg = "; ".join(warn_parts)
        if docker_note:
            msg = f"{msg} | docker_df={docker_note[:400]}"
        node.resource_warning_message = msg[:512]
        node.schedulable = False
    else:
        node.resource_status = ExecutionNodeResourceStatus.OK.value
        node.resource_warning_message = None
        if node.status == ExecutionNodeStatus.READY.value and prev_low:
            if not bool(getattr(settings, "devnest_require_fresh_node_heartbeat", False)):
                node.schedulable = True
            else:
                hb_ok, _hb_reason = execution_node_heartbeat_within_max_age(node, settings=settings)
                if hb_ok:
                    node.schedulable = True

    node.updated_at = now
    session.add(node)


def check_one_ec2_node_resources(
    session: Session,
    node: ExecutionNode,
    *,
    ssm_client: BaseClient | None = None,
    timeout_seconds: int = 120,
) -> None:
    """Run SSM probe and mutate ``node`` (caller commits)."""
    log_event(
        logger,
        LogEvent.NODE_RESOURCE_CHECK_STARTED,
        node_key=node.node_key,
        instance_id=(node.provider_instance_id or "").strip() or None,
    )
    region = (node.region or "").strip() or None
    client = ssm_client or build_ssm_client(region=region)
    iid = (node.provider_instance_id or "").strip()
    stdout, _stderr = send_run_shell_script(
        client,
        iid,
        [HOST_RESOURCE_PROBE_SCRIPT],
        comment="DevNest-host-resources",
        timeout_seconds=timeout_seconds,
    )
    parsed = parse_host_resource_ssm_stdout(stdout)
    docker_note = parsed.get("docker_system_df")
    docker_s = str(docker_note)[:450] if docker_note else None
    _apply_resource_metrics_to_node(session, node, parsed, docker_note=docker_s)
    log_event(
        logger,
        LogEvent.NODE_RESOURCE_CHECK_SUCCEEDED,
        node_key=node.node_key,
        instance_id=iid or None,
        disk_free_mb=node.disk_free_mb,
        memory_free_mb=node.memory_free_mb,
    )


def run_ec2_node_resource_monitor_tick(session: Session, *, ssm_client: BaseClient | None = None) -> int:
    """Check every READY EC2 node that uses SSM. Returns count of successful probes."""
    settings = get_settings()
    if not settings.devnest_node_resource_monitor_enabled:
        return 0
    stmt = (
        select(ExecutionNode)
        .where(
            and_(
                ExecutionNode.provider_type == ExecutionNodeProviderType.EC2.value,
                ExecutionNode.status == ExecutionNodeStatus.READY.value,
                ExecutionNode.execution_mode == ExecutionNodeExecutionMode.SSM_DOCKER.value,
            ),
        )
        .order_by(ExecutionNode.node_key.asc())
    )
    rows = list(session.exec(stmt).all())
    ok = 0
    for node in rows:
        iid = (node.provider_instance_id or "").strip()
        if not iid:
            continue
        try:
            check_one_ec2_node_resources(session, node, ssm_client=ssm_client)
            ok += 1
        except SsmExecutionError as e:
            log_event(
                logger,
                LogEvent.NODE_RESOURCE_CHECK_FAILED,
                node_key=node.node_key,
                instance_id=iid,
                detail=str(e)[:2000],
            )
            logger.warning(
                "node_resource_check_ssm_failed",
                extra={"node_key": node.node_key, "instance_id": iid, "error": str(e)[:500]},
            )
        except Exception as e:
            log_event(
                logger,
                LogEvent.NODE_RESOURCE_CHECK_FAILED,
                node_key=node.node_key,
                instance_id=iid,
                detail=str(e)[:2000],
            )
            logger.warning(
                "node_resource_check_failed",
                extra={"node_key": node.node_key, "instance_id": iid, "error": str(e)[:500]},
            )
    return ok
