"""Unit tests: internal execution-node routes (TestClient + SQLite)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.libs.db.database import get_db
from app.services.infrastructure_service.api.routers import internal_execution_nodes_router
from app.services.placement_service.bootstrap import default_local_node_key, ensure_default_local_execution_node
from app.services.placement_service.models import ExecutionNode, ExecutionNodeStatus

INTERNAL_HEADERS = {"X-Internal-API-Key": "unit-test-internal-key"}


@pytest.fixture
def internal_api_client(infrastructure_unit_engine: Engine, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "unit-test-internal-key")
    get_settings.cache_clear()

    mini = FastAPI()
    mini.include_router(internal_execution_nodes_router)

    def db_dep():
        db = Session(infrastructure_unit_engine)
        try:
            yield db
        finally:
            db.close()

    with Session(infrastructure_unit_engine) as s:
        ensure_default_local_execution_node(s)
        s.commit()

    mini.dependency_overrides[get_db] = db_dep
    with TestClient(mini) as client:
        yield client
    mini.dependency_overrides.clear()
    get_settings.cache_clear()


def test_drain_local_node_via_internal_api(internal_api_client: TestClient, infrastructure_unit_engine: Engine) -> None:
    key = default_local_node_key()
    r = internal_api_client.post(
        "/internal/execution-nodes/drain",
        json={"node_key": key},
        headers=INTERNAL_HEADERS,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["node_key"] == key
    assert data["status"] == ExecutionNodeStatus.DRAINING.value
    assert data["schedulable"] is False

    with Session(infrastructure_unit_engine) as session:
        row = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == key)).first()
        assert row is not None
        assert row.status == ExecutionNodeStatus.DRAINING.value


def test_list_execution_nodes_with_capacity(internal_api_client: TestClient) -> None:
    r = internal_api_client.get("/internal/execution-nodes/", headers=INTERNAL_HEADERS)
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    row0 = data[0]
    assert "node_key" in row0
    assert "max_workspaces" in row0
    assert "active_workspace_slots" in row0
    assert "available_workspace_slots" in row0
    assert row0["active_workspace_slots"] >= 0
    assert row0["available_workspace_slots"] >= 0


def test_internal_execution_nodes_requires_api_key(internal_api_client: TestClient) -> None:
    key = default_local_node_key()
    r = internal_api_client.post("/internal/execution-nodes/drain", json={"node_key": key})
    assert r.status_code == 401


def test_sync_body_requires_selector(internal_api_client: TestClient) -> None:
    r = internal_api_client.post(
        "/internal/execution-nodes/sync",
        json={},
        headers=INTERNAL_HEADERS,
    )
    assert r.status_code == 422
