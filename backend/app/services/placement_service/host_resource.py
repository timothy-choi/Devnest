"""Host disk/memory gates for EC2 execution nodes (SSM telemetry)."""

from __future__ import annotations

import logging

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.libs.observability.log_events import LogEvent, log_event

from .models import ExecutionNode, ExecutionNodeProviderType, ExecutionNodeResourceStatus, ExecutionNodeStatus

logger = logging.getLogger(__name__)

# Shell script: KEY=value lines for parsing (POSIX df/free).
HOST_RESOURCE_PROBE_SCRIPT = r"""set -eu
ROOT_LINE=$(df -m / | awk 'NR==2')
ROOT_TOTAL=$(echo "$ROOT_LINE" | awk '{print $2}')
ROOT_FREE=$(echo "$ROOT_LINE" | awk '{print $4}')
MEM_LINE=$(free -m | awk '/^Mem:/')
MEM_TOTAL=$(echo "$MEM_LINE" | awk '{print $2}')
MEM_AVAIL=$(echo "$MEM_LINE" | awk '{print $7}')
echo "DEVNEST_DISK_TOTAL_MB=${ROOT_TOTAL}"
echo "DEVNEST_DISK_FREE_MB=${ROOT_FREE}"
echo "DEVNEST_MEMORY_TOTAL_MB=${MEM_TOTAL}"
echo "DEVNEST_MEMORY_FREE_MB=${MEM_AVAIL}"
if command -v docker >/dev/null 2>&1; then
  DOCKER_SUMMARY=$(docker system df 2>/dev/null | head -n 12 | tr '\n' ' ' | head -c 450 || true)
  echo "DEVNEST_DOCKER_SYSTEM_DF=${DOCKER_SUMMARY}"
fi
"""


def resource_stale_cutoff_utc(*, check_interval_seconds: int | None = None) -> datetime:
    """Hosts must have been checked within this window to stay placement-eligible."""
    settings = get_settings()
    interval = int(
        check_interval_seconds
        if check_interval_seconds is not None
        else settings.devnest_node_resource_check_interval_seconds,
    )
    interval = max(10, interval)
    stale_seconds = max(interval * 3, interval + 120)
    return datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)


def ec2_host_resource_placement_predicates():
    """SQL fragments excluding EC2 nodes that fail host disk/memory/staleness checks."""
    settings = get_settings()
    if not settings.devnest_node_resource_monitor_enabled:
        return []
    min_disk = int(settings.devnest_node_min_free_disk_mb)
    min_mem = int(settings.devnest_node_min_free_memory_mb)
    cutoff = resource_stale_cutoff_utc()
    low_vals = (
        ExecutionNodeResourceStatus.LOW_DISK.value,
        ExecutionNodeResourceStatus.LOW_MEMORY.value,
    )
    return [
        or_(
            ExecutionNode.provider_type != ExecutionNodeProviderType.EC2.value,
            and_(
                ExecutionNode.disk_free_mb.isnot(None),
                ExecutionNode.memory_free_mb.isnot(None),
                ExecutionNode.disk_free_mb >= min_disk,
                ExecutionNode.memory_free_mb >= min_mem,
                ExecutionNode.last_resource_check_at.isnot(None),
                ExecutionNode.last_resource_check_at >= cutoff,
                or_(
                    ExecutionNode.resource_status.is_(None),
                    ExecutionNode.resource_status == "",
                    ExecutionNode.resource_status.notin_(low_vals),
                ),
            ),
        ),
    ]


def parse_host_resource_ssm_stdout(stdout: str) -> dict[str, int | str | None]:
    """Parse DEVNEST_* lines from SSM probe stdout."""
    out: dict[str, int | str | None] = {
        "disk_total_mb": None,
        "disk_free_mb": None,
        "memory_total_mb": None,
        "memory_free_mb": None,
        "docker_system_df": None,
    }
    for line in (stdout or "").splitlines():
        line = line.strip()
        if "=" not in line or not line.startswith("DEVNEST_"):
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if key == "DEVNEST_DISK_TOTAL_MB":
            out["disk_total_mb"] = _safe_pos_int(val)
        elif key == "DEVNEST_DISK_FREE_MB":
            out["disk_free_mb"] = _safe_pos_int(val)
        elif key == "DEVNEST_MEMORY_TOTAL_MB":
            out["memory_total_mb"] = _safe_pos_int(val)
        elif key == "DEVNEST_MEMORY_FREE_MB":
            out["memory_free_mb"] = _safe_pos_int(val)
        elif key == "DEVNEST_DOCKER_SYSTEM_DF":
            out["docker_system_df"] = val[:512] if val else None
    return out


def _safe_pos_int(raw: str) -> int | None:
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def ec2_node_host_resource_failure_reason(node: ExecutionNode) -> str | None:
    """Return a short reason if this EC2 node fails host resource gate (for diagnostics)."""
    settings = get_settings()
    if not settings.devnest_node_resource_monitor_enabled:
        return None
    if (node.provider_type or "").strip() != ExecutionNodeProviderType.EC2.value:
        return None
    rs = (node.resource_status or "").strip().upper()
    if rs in (
        ExecutionNodeResourceStatus.LOW_DISK.value,
        ExecutionNodeResourceStatus.LOW_MEMORY.value,
    ):
        return "resource_status"
    min_disk = int(settings.devnest_node_min_free_disk_mb)
    min_mem = int(settings.devnest_node_min_free_memory_mb)
    cutoff = resource_stale_cutoff_utc()
    if node.last_resource_check_at is None:
        return "no_check"
    ts = node.last_resource_check_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if ts < cutoff:
        return "stale_check"
    df = node.disk_free_mb
    mf = node.memory_free_mb
    if df is None or mf is None:
        return "missing_telemetry"
    if df < min_disk:
        return "low_disk"
    if mf < min_mem:
        return "low_memory"
    return None


def log_scheduler_skipped_ec2_nodes_for_host_resources(session: Session, *, limit: int = 24) -> None:
    """Emit scheduler.node.skipped_low_* when EC2 nodes fail disk/memory host gate."""
    settings = get_settings()
    if not settings.devnest_node_resource_monitor_enabled:
        return
    stmt = (
        select(ExecutionNode)
        .where(
            and_(
                ExecutionNode.provider_type == ExecutionNodeProviderType.EC2.value,
                ExecutionNode.status == ExecutionNodeStatus.READY.value,
            ),
        )
        .order_by(ExecutionNode.node_key.asc())
        .limit(max(1, min(limit, 100)))
    )
    for node in session.exec(stmt).all():
        reason = ec2_node_host_resource_failure_reason(node)
        nk = (node.node_key or "").strip()
        iid = (node.provider_instance_id or "").strip() or None
        base = {"node_key": nk, "instance_id": iid}
        rs = (node.resource_status or "").strip().upper()
        if reason == "low_disk" or rs == ExecutionNodeResourceStatus.LOW_DISK.value:
            log_event(logger, LogEvent.SCHEDULER_NODE_SKIPPED_LOW_DISK, **base)
        elif reason == "low_memory" or rs == ExecutionNodeResourceStatus.LOW_MEMORY.value:
            log_event(logger, LogEvent.SCHEDULER_NODE_SKIPPED_LOW_MEMORY, **base)

