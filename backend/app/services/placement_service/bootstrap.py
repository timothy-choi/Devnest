"""Seed a default local execution node for dev/tests (no EC2)."""

from __future__ import annotations

import os
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.libs.topology.models import Topology

from .constants import (
    DEFAULT_EXECUTION_NODE_ALLOCATABLE_DISK_MB,
    DEFAULT_EXECUTION_NODE_MAX_WORKSPACES,
)
from .models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
    ExecutionNodeStatus,
)

logger = logging.getLogger(__name__)


def default_local_node_key() -> str:
    return (os.environ.get("DEVNEST_NODE_ID") or "node-1").strip() or "node-1"


def _dev_default_topology_id() -> int:
    raw = (os.environ.get("DEVNEST_TOPOLOGY_ID") or "1").strip()
    try:
        return int(raw, 10)
    except ValueError:
        return 1


def system_default_topology_id() -> int:
    """System default topology id for execution nodes; independent of scheduling env fallback."""
    return 1


def _is_development_env() -> bool:
    settings = get_settings()
    return str(settings.devnest_env or "development").strip().lower() == "development"


def _sync_topology_pk_sequence(session: Session) -> None:
    """Advance PostgreSQL ``topology.topology_id`` sequence after explicit PK inserts.

    Bootstrap inserts ``Topology(topology_id=DEVNEST_TOPOLOGY_ID)`` without bumping the
    underlying sequence; the next INSERT that omits ``topology_id`` would still try ``1`` and
    hit ``UniqueViolation``. SQLite tests skip this (no serial sequence).
    """
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    session.execute(
        text(
            "SELECT setval("
            "pg_get_serial_sequence('topology', 'topology_id'), "
            "(SELECT COALESCE(MAX(topology_id), 1) FROM topology), "
            "true)",
        ),
    )


def ensure_topology_row_for_local_dev(session: Session, topology_id: int) -> None:
    """Ensure a ``Topology`` catalog row exists for ``topology_id`` in development.

    Local bootstrap wires ``ExecutionNode.default_topology_id`` to ``DEVNEST_TOPOLOGY_ID`` (default
    ``1``). Without a matching ``topology`` row, workspace bring-up fails late with
    ``TopologyRuntimeCreateError: topology id N not found``. This keeps dev databases self-consistent.
    """
    if not _is_development_env():
        return
    if session.get(Topology, topology_id) is None:
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
    _sync_topology_pk_sequence(session)


def ensure_execution_node_default_topology(session: Session, node: ExecutionNode) -> bool:
    """Assign the system default topology to a node if missing. Returns True when changed."""
    if node.default_topology_id is not None:
        return False
    node.default_topology_id = system_default_topology_id()
    session.add(node)
    session.flush()
    if _is_development_env():
        ensure_topology_row_for_local_dev(session, int(node.default_topology_id))
    logger.info(
        "execution_node.topology.assigned",
        extra={
            "node_key": node.node_key,
            "execution_node_id": node.id,
            "default_topology_id": node.default_topology_id,
        },
    )
    return True


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
        changed = False
        if existing.default_topology_id is None:
            changed = ensure_execution_node_default_topology(session, existing)
        if int(existing.max_workspaces or 0) <= 0:
            existing.max_workspaces = DEFAULT_EXECUTION_NODE_MAX_WORKSPACES
            changed = True
        if int(existing.allocatable_disk_mb or 0) <= 0:
            existing.allocatable_disk_mb = DEFAULT_EXECUTION_NODE_ALLOCATABLE_DISK_MB
            changed = True
        if changed:
            session.add(existing)
            session.flush()
        if dev and existing.default_topology_id is not None:
            ensure_topology_row_for_local_dev(session, int(existing.default_topology_id))
        return existing

    host_hint = (settings.database_url or "").split("@")[-1].split("/")[0] if settings.database_url else ""
    topo_id = system_default_topology_id()
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
        max_workspaces=DEFAULT_EXECUTION_NODE_MAX_WORKSPACES,
        allocatable_disk_mb=DEFAULT_EXECUTION_NODE_ALLOCATABLE_DISK_MB,
        metadata_json={"bootstrap": "local_v1", "db_host_hint": host_hint or None},
        default_topology_id=topo_id,
    )
    session.add(node)
    session.flush()
    if topo_id is not None:
        ensure_topology_row_for_local_dev(session, int(topo_id))
    return node
