"""Seed a default local execution node for dev/tests (no EC2)."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.libs.topology.models import Topology

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


def _is_development_env() -> bool:
    settings = get_settings()
    return str(settings.devnest_env or "development").strip().lower() == "development"


def ensure_topology_row_for_local_dev(session: Session, topology_id: int) -> None:
    """Ensure a ``Topology`` catalog row exists for ``topology_id`` in development.

    Local bootstrap wires ``ExecutionNode.default_topology_id`` to ``DEVNEST_TOPOLOGY_ID`` (default
    ``1``). Without a matching ``topology`` row, workspace bring-up fails late with
    ``TopologyRuntimeCreateError: topology id N not found``. This keeps dev databases self-consistent.
    """
    if not _is_development_env():
        return
    if session.get(Topology, topology_id) is not None:
        return
    now = datetime.now(timezone.utc)
    session.add(
        Topology(
            topology_id=topology_id,
            name=f"dev-local-topology-{topology_id}",
            version="v1",
            spec_json={},
            created_at=now,
            updated_at=now,
        ),
    )
    session.flush()


def ensure_default_local_execution_node(session: Session) -> ExecutionNode:
    """
    Idempotently ensure the configured local node row exists.

    Called from :func:`app.libs.db.database.init_db` and integration test DB cleanup.
    """
    key = default_local_node_key()
    settings = get_settings()
    dev = _is_development_env()

    existing = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == key)).first()
    if existing is not None:
        if dev and existing.default_topology_id is None:
            existing.default_topology_id = _dev_default_topology_id()
            session.add(existing)
            session.flush()
        if dev and existing.default_topology_id is not None:
            ensure_topology_row_for_local_dev(session, int(existing.default_topology_id))
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
    if topo_id is not None:
        ensure_topology_row_for_local_dev(session, int(topo_id))
    return node
