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


def test_drain_local_node_via_internal_api(client, db_session: Session) -> None:
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


def test_internal_execution_nodes_requires_api_key(client, db_session: Session) -> None:
    db_session.commit()
    key = default_local_node_key()
    r = client.post("/internal/execution-nodes/drain", json={"node_key": key})
    assert r.status_code == 401
