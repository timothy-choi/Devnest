"""
Integration tests: internal execution-node lifecycle routes (PostgreSQL + full app).

Requires a local Postgres test user/database (see ``tests/integration/conftest.py``).
For CI-free local runs, see ``tests/unit/infrastructure/test_internal_execution_nodes_routes.py``.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.services.placement_service.bootstrap import default_local_node_key
from app.services.placement_service.models import ExecutionNode, ExecutionNodeStatus

INTERNAL_HEADERS = {"X-Internal-API-Key": "integration-test-internal-key"}


def test_drain_then_undrain_local_node_via_internal_api(client, db_session: Session) -> None:
    key = default_local_node_key()
    r = client.post("/internal/execution-nodes/drain", json={"node_key": key}, headers=INTERNAL_HEADERS)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["node_key"] == key
    assert data["status"] == ExecutionNodeStatus.DRAINING.value
    assert data["schedulable"] is False

    row = db_session.exec(select(ExecutionNode).where(ExecutionNode.node_key == key)).first()
    assert row is not None
    assert row.status == ExecutionNodeStatus.DRAINING.value

    r2 = client.post("/internal/execution-nodes/undrain", json={"node_key": key}, headers=INTERNAL_HEADERS)
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == ExecutionNodeStatus.READY.value
    assert r2.json()["schedulable"] is True


def test_internal_execution_nodes_requires_api_key(client, db_session: Session) -> None:
    db_session.commit()
    key = default_local_node_key()
    r = client.post("/internal/execution-nodes/drain", json={"node_key": key})
    assert r.status_code == 401


def test_post_heartbeat_updates_execution_node_row(client, db_session: Session) -> None:
    """Full-app path: infrastructure-scoped key + POST heartbeat updates DB (Phase 3a)."""
    key = default_local_node_key()
    r = client.post(
        "/internal/execution-nodes/heartbeat",
        json={
            "node_key": key,
            "docker_ok": True,
            "disk_free_mb": 42_000,
            "slots_in_use": 1,
            "version": "integration-api-test",
        },
        headers=INTERNAL_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["node_key"] == key
    assert body["last_heartbeat_at"] is not None
    assert set(body.keys()) == {"id", "node_key", "status", "schedulable", "last_heartbeat_at"}

    db_session.expire_all()
    row = db_session.exec(select(ExecutionNode).where(ExecutionNode.node_key == key)).first()
    assert row is not None
    assert row.last_heartbeat_at is not None
    hb = (row.metadata_json or {}).get("heartbeat") or {}
    assert hb.get("version") == "integration-api-test"
    assert hb.get("disk_free_mb") == 42_000
    assert hb.get("slots_in_use") == 1
