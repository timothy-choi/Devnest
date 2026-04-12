"""Unit tests: scheduler policy (effective free capacity)."""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.services.placement_service.models import ExecutionNode, ExecutionNodeProviderType, ExecutionNodeStatus
from app.services.scheduler_service.models import WorkspaceComputeRequest
from app.services.scheduler_service.policy import (
    can_fit_workspace,
    rank_candidate_nodes,
    scheduling_sort_key,
    scheduling_sort_key_effective,
)


@pytest.fixture
def policy_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _node(
    *,
    key: str,
    alloc_cpu: float,
    alloc_mem: int,
) -> ExecutionNode:
    return ExecutionNode(
        node_key=key,
        name=key,
        provider_type=ExecutionNodeProviderType.LOCAL.value,
        status=ExecutionNodeStatus.READY.value,
        schedulable=True,
        total_cpu=max(alloc_cpu, 0.25),
        total_memory_mb=max(alloc_mem, 256),
        allocatable_cpu=alloc_cpu,
        allocatable_memory_mb=alloc_mem,
    )


def test_can_fit_workspace_requires_cpu_and_memory() -> None:
    req = WorkspaceComputeRequest(requested_cpu=1.0, requested_memory_mb=512)
    assert can_fit_workspace(_node(key="a", alloc_cpu=2.0, alloc_mem=1024), req) is True
    assert can_fit_workspace(_node(key="b", alloc_cpu=0.5, alloc_mem=1024), req) is False
    assert can_fit_workspace(_node(key="c", alloc_cpu=2.0, alloc_mem=256), req) is False


def test_rank_candidate_nodes_excludes_not_ready_or_not_schedulable(policy_session: Session) -> None:
    req = WorkspaceComputeRequest(requested_cpu=0.5, requested_memory_mb=256)
    bad_status = _node(key="bad-status", alloc_cpu=8.0, alloc_mem=8192)
    bad_status.status = ExecutionNodeStatus.DRAINING.value
    bad_sched = _node(key="bad-sched", alloc_cpu=8.0, alloc_mem=8192)
    bad_sched.schedulable = False
    ok = _node(key="ok", alloc_cpu=4.0, alloc_mem=4096)
    out = rank_candidate_nodes(policy_session, [bad_status, bad_sched, ok], req)
    assert [n.node_key for n in out] == ["ok"]


def test_rank_candidate_nodes_best_fit_then_lexicographic(policy_session: Session) -> None:
    req = WorkspaceComputeRequest(requested_cpu=0.5, requested_memory_mb=256)
    n_big = _node(key="z", alloc_cpu=4.0, alloc_mem=8192)
    n_small = _node(key="a", alloc_cpu=4.0, alloc_mem=4096)
    n_tiny_cpu = _node(key="m", alloc_cpu=8.0, alloc_mem=512)
    out = rank_candidate_nodes(policy_session, [n_small, n_big, n_tiny_cpu], req)
    assert out[0].node_key == "m"
    assert out[1].node_key == "z"
    assert out[2].node_key == "a"


def test_scheduling_sort_key_matches_rank_order(policy_session: Session) -> None:
    from app.services.placement_service.capacity import total_reserved_on_node_key

    req = WorkspaceComputeRequest(requested_cpu=0.1, requested_memory_mb=128)
    nodes = [_node(key="b", alloc_cpu=2.0, alloc_mem=1000), _node(key="a", alloc_cpu=2.0, alloc_mem=1000)]
    ranked = rank_candidate_nodes(policy_session, nodes, req)

    def _eff_key(n: ExecutionNode) -> tuple:
        uc, um = total_reserved_on_node_key(policy_session, (n.node_key or "").strip())
        fc = max(0.0, float(n.allocatable_cpu or 0.0) - uc)
        fm = max(0, int(n.allocatable_memory_mb or 0) - um)
        return scheduling_sort_key_effective(fc, fm, (n.node_key or "").strip())

    assert ranked == sorted(ranked, key=_eff_key)
    assert sorted(ranked, key=scheduling_sort_key) == ranked


def test_rank_excludes_node_when_reservations_consume_capacity(policy_session: Session) -> None:
    from app.services.auth_service.models.user_auth import UserAuth
    from app.services.workspace_service.models import Workspace, WorkspaceRuntime
    from app.services.workspace_service.models.enums import WorkspaceStatus

    u = UserAuth(username="u1", password_hash="x", email="u1@e.com")
    policy_session.add(u)
    policy_session.commit()
    policy_session.refresh(u)
    assert u.user_auth_id is not None

    ws = Workspace(
        name="w",
        owner_user_id=u.user_auth_id,
        status=WorkspaceStatus.RUNNING.value,
    )
    policy_session.add(ws)
    policy_session.commit()
    policy_session.refresh(ws)
    assert ws.workspace_id is not None

    n = _node(key="hot", alloc_cpu=2.0, alloc_mem=2048)
    policy_session.add(n)
    policy_session.commit()

    rt = WorkspaceRuntime(
        workspace_id=ws.workspace_id,
        node_id="hot",
        reserved_cpu=1.5,
        reserved_memory_mb=1536,
    )
    policy_session.add(rt)
    policy_session.commit()

    req = WorkspaceComputeRequest(requested_cpu=1.0, requested_memory_mb=1024)
    out = rank_candidate_nodes(policy_session, [n], req)
    assert out == []

    req2 = WorkspaceComputeRequest(requested_cpu=0.25, requested_memory_mb=256)
    out2 = rank_candidate_nodes(policy_session, [n], req2)
    assert len(out2) == 1 and out2[0].node_key == "hot"
