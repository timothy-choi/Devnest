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


def ensure_default_local_execution_node(session: Session) -> ExecutionNode:
    """
    Idempotently ensure the configured local node row exists.

    Called from :func:`app.libs.db.database.init_db` and integration test DB cleanup.
    """
    key = default_local_node_key()
    existing = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == key)).first()
    if existing is not None:
        return existing

    settings = get_settings()
    host_hint = (settings.database_url or "").split("@")[-1].split("/")[0] if settings.database_url else ""
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
        default_topology_id=None,
    )
    session.add(node)
    session.flush()
    return node
