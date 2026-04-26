"""Unit tests: internal execution-node routes (TestClient + SQLite)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.libs.db.database import get_db
from app.services.infrastructure_service.api.routers import internal_execution_nodes_router
from app.services.placement_service.bootstrap import default_local_node_key, ensure_default_local_execution_node
from app.services.auth_service.models import UserAuth
from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
    ExecutionNodeStatus,
)
from app.services.workspace_service.models import Workspace, WorkspaceRuntime
from app.services.workspace_service.models.enums import WorkspaceStatus

INTERNAL_HEADERS = {"X-Internal-API-Key": "unit-test-internal-key"}


def test_internal_execution_nodes_router_registers_post_heartbeat() -> None:
    paths = [getattr(r, "path", "") for r in internal_execution_nodes_router.routes]
    assert "/internal/execution-nodes/register-catalog-ec2" in paths
    assert "/internal/execution-nodes/heartbeat" in paths
    hb_routes = [r for r in internal_execution_nodes_router.routes if getattr(r, "path", "") == "/internal/execution-nodes/heartbeat"]
    assert hb_routes and "POST" in (getattr(hb_routes[0], "methods", set()) or set())


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


def test_drain_then_undrain_local_node_via_internal_api(
    internal_api_client: TestClient,
    infrastructure_unit_engine: Engine,
) -> None:
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

    r2 = internal_api_client.post(
        "/internal/execution-nodes/undrain",
        json={"node_key": key},
        headers=INTERNAL_HEADERS,
    )
    assert r2.status_code == 200, r2.text
    data2 = r2.json()
    assert data2["node_key"] == key
    assert data2["status"] == ExecutionNodeStatus.READY.value
    assert data2["schedulable"] is True


def test_register_catalog_ec2_stub_via_internal_api(
    internal_api_client: TestClient,
    infrastructure_unit_engine: Engine,
) -> None:
    r = internal_api_client.post(
        "/internal/execution-nodes/register-catalog-ec2",
        json={
            "node_key": "node-2",
            "name": "catalog node 2",
            "region": "us-east-1",
            "private_ip": "10.0.2.20",
            "public_ip": "198.51.100.2",
            "provider_instance_id": "i-0catalogstub00001",
            "execution_mode": "ssm_docker",
            "status": "NOT_READY",
        },
        headers=INTERNAL_HEADERS,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["node_key"] == "node-2"
    assert data["schedulable"] is False
    assert data["status"] == ExecutionNodeStatus.NOT_READY.value
    assert data["provider_type"] == "ec2"
    with Session(infrastructure_unit_engine) as session:
        row = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == "node-2")).first()
        assert row is not None
        assert row.schedulable is False


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
    assert "heartbeat_age_seconds" in row0
    assert row0["heartbeat_age_seconds"] is None or isinstance(row0["heartbeat_age_seconds"], int)
    assert "metadata_json" not in row0
    assert "ssh_host" not in row0
    assert "ssh_user" not in row0


def test_undrain_terminated_returns_409(internal_api_client: TestClient, infrastructure_unit_engine: Engine) -> None:
    with Session(infrastructure_unit_engine) as session:
        session.add(
            ExecutionNode(
                node_key="term-undrain",
                name="term-undrain",
                provider_type=ExecutionNodeProviderType.LOCAL.value,
                execution_mode=ExecutionNodeExecutionMode.LOCAL_DOCKER.value,
                status=ExecutionNodeStatus.TERMINATED.value,
                schedulable=False,
                total_cpu=2.0,
                total_memory_mb=4096,
                allocatable_cpu=2.0,
                allocatable_memory_mb=4096,
                metadata_json={},
            ),
        )
        session.commit()
    r = internal_api_client.post(
        "/internal/execution-nodes/undrain",
        json={"node_key": "term-undrain"},
        headers=INTERNAL_HEADERS,
    )
    assert r.status_code == 409


def test_workspaces_by_node_groups_runtime_pins(
    internal_api_client: TestClient,
    infrastructure_unit_engine: Engine,
) -> None:
    key = default_local_node_key()
    with Session(infrastructure_unit_engine) as session:
        u = UserAuth(username="ops_ws", email="ops_ws@e.com", password_hash="x")
        session.add(u)
        session.commit()
        session.refresh(u)
        now = datetime.now(timezone.utc)
        ws = Workspace(
            name="ops-inventory-ws",
            owner_user_id=u.user_auth_id,
            status=WorkspaceStatus.RUNNING.value,
            is_private=True,
            created_at=now,
            updated_at=now,
        )
        session.add(ws)
        session.flush()
        session.add(
            WorkspaceRuntime(
                workspace_id=ws.workspace_id,
                node_id=key,
                container_id="c1",
                container_state="running",
                topology_id=1,
                internal_endpoint="http://10.0.0.1:8080",
                config_version=1,
            ),
        )
        session.commit()

    r = internal_api_client.get("/internal/execution-nodes/workspaces-by-node", headers=INTERNAL_HEADERS)
    assert r.status_code == 200, r.text
    buckets = r.json()
    assert isinstance(buckets, list)
    match = next((b for b in buckets if b.get("node_key") == key), None)
    assert match is not None
    assert match["workspace_count"] >= 1
    assert any(w["name"] == "ops-inventory-ws" for w in match["workspaces"])


def test_internal_execution_nodes_requires_api_key(internal_api_client: TestClient) -> None:
    key = default_local_node_key()
    r = internal_api_client.post("/internal/execution-nodes/drain", json={"node_key": key})
    assert r.status_code == 401


def test_post_heartbeat_updates_node(
    internal_api_client: TestClient,
    infrastructure_unit_engine: Engine,
) -> None:
    key = default_local_node_key()
    r = internal_api_client.post(
        "/internal/execution-nodes/heartbeat",
        json={
            "node_key": key,
            "docker_ok": True,
            "disk_free_mb": 12345,
            "slots_in_use": 2,
            "version": "test-unit",
        },
        headers=INTERNAL_HEADERS,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data.keys()) == {"id", "node_key", "status", "schedulable", "last_heartbeat_at"}
    assert data["last_heartbeat_at"] is not None
    assert data["node_key"] == key
    with Session(infrastructure_unit_engine) as s:
        row = s.exec(select(ExecutionNode).where(ExecutionNode.node_key == key)).first()
        assert row is not None
        hb = (row.metadata_json or {}).get("heartbeat") or {}
        assert hb.get("version") == "test-unit"
        assert hb.get("disk_free_mb") == 12345
        assert hb.get("slots_in_use") == 2


def test_post_heartbeat_docker_false_sets_error(
    internal_api_client: TestClient,
    infrastructure_unit_engine: Engine,
) -> None:
    key = default_local_node_key()
    r = internal_api_client.post(
        "/internal/execution-nodes/heartbeat",
        json={
            "node_key": key,
            "docker_ok": False,
            "disk_free_mb": 100,
            "slots_in_use": 0,
            "version": "test-unit",
        },
        headers=INTERNAL_HEADERS,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["node_key"] == key
    with Session(infrastructure_unit_engine) as s:
        row = s.exec(select(ExecutionNode).where(ExecutionNode.node_key == key)).first()
        assert row is not None
        assert row.last_error_code == "DOCKER_UNREACHABLE"
        assert (row.metadata_json or {}).get("heartbeat", {}).get("docker_ok") is False


def test_post_heartbeat_minimal_response_matches_contract(internal_api_client: TestClient) -> None:
    key = default_local_node_key()
    r = internal_api_client.post(
        "/internal/execution-nodes/heartbeat",
        json={
            "node_key": key,
            "docker_ok": True,
            "disk_free_mb": 50,
            "slots_in_use": 0,
            "version": "contract-check",
        },
        headers=INTERNAL_HEADERS,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data.keys()) == {"id", "node_key", "status", "schedulable", "last_heartbeat_at"}
    assert data["id"] is not None
    assert data["node_key"] == key


def test_post_heartbeat_bootstrap_default_node_when_table_empty(
    infrastructure_unit_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty execution_node table: default local node_key still returns 200 (bootstrap in handler)."""
    monkeypatch.setenv("INTERNAL_API_KEY", "unit-test-internal-key")
    get_settings.cache_clear()
    with Session(infrastructure_unit_engine) as s:
        for row in list(s.exec(select(ExecutionNode)).all()):
            s.delete(row)
        s.commit()

    mini = FastAPI()
    mini.include_router(internal_execution_nodes_router)

    def db_dep():
        db = Session(infrastructure_unit_engine)
        try:
            yield db
        finally:
            db.close()

    mini.dependency_overrides[get_db] = db_dep
    key = default_local_node_key()
    with TestClient(mini) as client:
        r = client.post(
            "/internal/execution-nodes/heartbeat",
            json={
                "node_key": key,
                "docker_ok": True,
                "disk_free_mb": 100,
                "slots_in_use": 0,
                "version": "cold-db-bootstrap",
            },
            headers=INTERNAL_HEADERS,
        )
    assert r.status_code == 200, r.text
    assert r.json()["node_key"] == key
    assert r.json()["last_heartbeat_at"] is not None


def test_post_heartbeat_unknown_node_returns_404(internal_api_client: TestClient) -> None:
    r = internal_api_client.post(
        "/internal/execution-nodes/heartbeat",
        json={
            "node_key": "no-such-node-xyz",
            "docker_ok": True,
            "disk_free_mb": 1,
            "slots_in_use": 0,
            "version": "v",
        },
        headers=INTERNAL_HEADERS,
    )
    assert r.status_code == 404


def test_sync_body_requires_selector(internal_api_client: TestClient) -> None:
    r = internal_api_client.post(
        "/internal/execution-nodes/sync",
        json={},
        headers=INTERNAL_HEADERS,
    )
    assert r.status_code == 422
