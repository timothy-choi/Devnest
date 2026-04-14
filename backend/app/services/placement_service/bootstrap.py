"""Seed a default local execution node for dev/tests (no EC2)."""

from __future__ import annotations

import os

from sqlmodel import Session, select

from app.libs.common.config import get_settings

from .models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
    ExecutionNodeStatus,
)


def default_local_node_key() -> str:
    return (os.environ.get("DEVNEST_NODE_ID") or "node-1").strip() or "node-1"


def _dev_default_topology_id() -> int:
    raw = (os.environ.get("DEVNEST_TOPOLOGY_ID") or "1").strip()
    try:
        return int(raw, 10)
    except ValueError:
        return 1


def ensure_default_local_execution_node(session: Session) -> ExecutionNode:
    """
    Idempotently ensure the configured local node row exists.

    Called from :func:`app.libs.db.database.init_db` and integration test DB cleanup.
    """
    key = default_local_node_key()
    settings = get_settings()
    dev = str(settings.devnest_env or "development").strip().lower() == "development"

    existing = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == key)).first()
    if existing is not None:
        if dev and existing.default_topology_id is None:
            existing.default_topology_id = _dev_default_topology_id()
            session.add(existing)
            session.flush()
        return existing

    host_hint = (settings.database_url or "").split("@")[-1].split("/")[0] if settings.database_url else ""
    topo_id = _dev_default_topology_id() if dev else None
    node = ExecutionNode(
        node_key=key,
        name=f"local-{key}",
        provider_type=ExecutionNodeProviderType.LOCAL.value,
        provider_instance_id=None,
        execution_mode=ExecutionNodeExecutionMode.LOCAL_DOCKER.value,
        hostname="localhost",
        private_ip=None,
        status=ExecutionNodeStatus.READY.value,
        schedulable=True,
        total_cpu=4.0,
        total_memory_mb=8192,
        allocatable_cpu=4.0,
        allocatable_memory_mb=8192,
        metadata_json={"bootstrap": "local_v1", "db_host_hint": host_hint or None},
        default_topology_id=topo_id,
    )
    session.add(node)
    session.flush()
    return node
