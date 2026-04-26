"""Internal pinned operator test workspace route (Phase 3b Step 8)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.libs.common.config import get_settings
from app.libs.db.database import get_db
from app.libs.topology.models import Topology
from app.services.auth_service.models import UserAuth
from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
    ExecutionNodeStatus,
)
from app.services.workspace_service.api.routers.internal_operator_test_workspaces import (
    router as internal_operator_test_workspaces_router,
)

INTERNAL_HEADERS = {"X-Internal-API-Key": "unit-test-internal-key"}


def _ensure_topology(session: Session, topology_id: int = 1) -> None:
    if session.get(Topology, topology_id) is not None:
        return
    now = datetime.now(timezone.utc)
    session.add(
        Topology(
            topology_id=topology_id,
            name=f"test-topology-{topology_id}",
            version="v1",
            spec_json={},
            created_at=now,
            updated_at=now,
        ),
    )
    session.commit()


def test_pinned_operator_create_403_when_disabled(
    infrastructure_unit_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "unit-test-internal-key")
    monkeypatch.setenv("DEVNEST_ALLOW_PINNED_CREATE_PLACEMENT", "false")
    monkeypatch.setenv("DEVNEST_PINNED_CREATE_EXECUTION_NODE_IDS", "2")
    get_settings.cache_clear()

    mini = FastAPI()
    mini.include_router(internal_operator_test_workspaces_router)

    def db_dep():
        db = Session(infrastructure_unit_engine)
        try:
            yield db
        finally:
            db.close()

    with Session(infrastructure_unit_engine) as s:
        _ensure_topology(s, 1)
        u = UserAuth(username="pin_owner2", email="pin_owner2@test", password_hash="x")
        s.add(u)
        s.commit()
        s.refresh(u)
        owner_id = int(u.user_auth_id)
        n = ExecutionNode(
            node_key="ec2-single",
            name="ec2-single",
            provider_type=ExecutionNodeProviderType.EC2.value,
            provider_instance_id="i-03333333333333333",
            region="us-east-1",
            execution_mode=ExecutionNodeExecutionMode.SSM_DOCKER.value,
            ssh_user="ubuntu",
            status=ExecutionNodeStatus.READY.value,
            schedulable=True,
            total_cpu=8.0,
            total_memory_mb=16384,
            allocatable_cpu=8.0,
            allocatable_memory_mb=16384,
            allocatable_disk_mb=102_400,
            max_workspaces=32,
            default_topology_id=1,
        )
        s.add(n)
        s.commit()
        s.refresh(n)
        node_pk = int(n.id)

    mini.dependency_overrides[get_db] = db_dep
    with TestClient(mini) as client:
        r = client.post(
            "/internal/test-workspaces/pinned-operator-create",
            json={"owner_user_id": owner_id, "execution_node_id": node_pk},
            headers=INTERNAL_HEADERS,
        )
    assert r.status_code == 403
    mini.dependency_overrides.clear()
    get_settings.cache_clear()


def test_pinned_operator_workspace_name_prefix(
    infrastructure_unit_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After accepted create, workspace row uses the pinned name prefix."""
    monkeypatch.setenv("INTERNAL_API_KEY", "unit-test-internal-key")
    monkeypatch.setenv("DEVNEST_ALLOW_PINNED_CREATE_PLACEMENT", "true")
    monkeypatch.setenv("DEVNEST_PINNED_CREATE_EXECUTION_NODE_IDS", "1")
    monkeypatch.setenv("DEVNEST_GATEWAY_ENABLED", "false")
    get_settings.cache_clear()

    mini = FastAPI()
    mini.include_router(internal_operator_test_workspaces_router)

    def db_dep():
        db = Session(infrastructure_unit_engine)
        try:
            yield db
        finally:
            db.close()

    with Session(infrastructure_unit_engine) as s:
        _ensure_topology(s, 1)
        u = UserAuth(username="pin_owner3", email="pin_owner3@test", password_hash="x")
        s.add(u)
        s.commit()
        s.refresh(u)
        owner_id = int(u.user_auth_id)
        n = ExecutionNode(
            node_key="ec2-only-one",
            name="ec2-only-one",
            provider_type=ExecutionNodeProviderType.EC2.value,
            provider_instance_id="i-04444444444444444",
            region="us-east-1",
            execution_mode=ExecutionNodeExecutionMode.SSM_DOCKER.value,
            ssh_user="ubuntu",
            status=ExecutionNodeStatus.READY.value,
            schedulable=True,
            total_cpu=8.0,
            total_memory_mb=16384,
            allocatable_cpu=8.0,
            allocatable_memory_mb=16384,
            allocatable_disk_mb=102_400,
            max_workspaces=32,
            default_topology_id=1,
        )
        s.add(n)
        s.commit()
        s.refresh(n)
        node_pk = int(n.id)

    mini.dependency_overrides[get_db] = db_dep
    with TestClient(mini) as client:
        r = client.post(
            "/internal/test-workspaces/pinned-operator-create",
            json={"owner_user_id": owner_id, "execution_node_id": node_pk},
            headers=INTERNAL_HEADERS,
        )
    assert r.status_code == 202, r.text
    wid = int(r.json()["workspace_id"])
    with Session(infrastructure_unit_engine) as s:
        from app.services.workspace_service.models import Workspace

        ws = s.get(Workspace, wid)
        assert ws is not None
        assert ws.name.startswith("devnest-op-pinned-test-")
        assert int(ws.execution_node_id) == node_pk
    mini.dependency_overrides.clear()
    get_settings.cache_clear()
