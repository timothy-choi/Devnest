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
    alloc_disk: int = 102_400,
    max_workspaces: int = 32,
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
        allocatable_disk_mb=alloc_disk,
        max_workspaces=max_workspaces,
    )


def test_can_fit_workspace_requires_cpu_memory_disk_and_slots() -> None:
    req = WorkspaceComputeRequest(requested_cpu=1.0, requested_memory_mb=512, requested_disk_mb=4096)
    assert can_fit_workspace(_node(key="a", alloc_cpu=2.0, alloc_mem=1024), req) is True
    assert can_fit_workspace(_node(key="b", alloc_cpu=0.5, alloc_mem=1024), req) is False
    assert can_fit_workspace(_node(key="c", alloc_cpu=2.0, alloc_mem=256), req) is False
    assert can_fit_workspace(_node(key="d", alloc_cpu=2.0, alloc_mem=1024, alloc_disk=1024), req) is False
    assert can_fit_workspace(_node(key="e", alloc_cpu=2.0, alloc_mem=1024, max_workspaces=0), req) is False


def test_rank_candidate_nodes_excludes_not_ready_or_not_schedulable(policy_session: Session) -> None:
    req = WorkspaceComputeRequest(requested_cpu=0.5, requested_memory_mb=256, requested_disk_mb=1024)
    bad_status = _node(key="bad-status", alloc_cpu=8.0, alloc_mem=8192)
    bad_status.status = ExecutionNodeStatus.DRAINING.value
    bad_sched = _node(key="bad-sched", alloc_cpu=8.0, alloc_mem=8192)
    bad_sched.schedulable = False
    ok = _node(key="ok", alloc_cpu=4.0, alloc_mem=4096)
    out = rank_candidate_nodes(policy_session, [bad_status, bad_sched, ok], req)
    assert [n.node_key for n in out] == ["ok"]


def test_rank_candidate_nodes_best_fit_then_lexicographic(policy_session: Session) -> None:
    req = WorkspaceComputeRequest(requested_cpu=0.5, requested_memory_mb=256, requested_disk_mb=1024)
    n_big = _node(key="z", alloc_cpu=4.0, alloc_mem=8192)
    n_small = _node(key="a", alloc_cpu=4.0, alloc_mem=4096)
    n_tiny_cpu = _node(key="m", alloc_cpu=8.0, alloc_mem=512)
    out = rank_candidate_nodes(policy_session, [n_small, n_big, n_tiny_cpu], req)
    assert out[0].node_key == "m"
    assert out[1].node_key == "z"
    assert out[2].node_key == "a"


def test_scheduling_sort_key_matches_rank_order(policy_session: Session) -> None:
    from app.services.placement_service.capacity import total_reserved_on_node_key

    req = WorkspaceComputeRequest(requested_cpu=0.1, requested_memory_mb=128, requested_disk_mb=512)
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
        reserved_disk_mb=8192,
    )
    policy_session.add(rt)
    policy_session.commit()

    req = WorkspaceComputeRequest(requested_cpu=1.0, requested_memory_mb=1024, requested_disk_mb=4096)
    out = rank_candidate_nodes(policy_session, [n], req)
    assert out == []

    req2 = WorkspaceComputeRequest(requested_cpu=0.25, requested_memory_mb=256, requested_disk_mb=1024)
    out2 = rank_candidate_nodes(policy_session, [n], req2)
    assert len(out2) == 1 and out2[0].node_key == "hot"


# ---------------------------------------------------------------------------
# Spread / fairness tests (new scheduling_sort_key_spread behaviour)
# ---------------------------------------------------------------------------

def _seed_active_workload(
    session: Session,
    *,
    node_key: str,
    cpu: float = 0.1,
    mem: int = 64,
    disk: int = 1024,
) -> None:
    """Seed a RUNNING workspace pinned to node_key (counts toward active workload count)."""
    from datetime import datetime, timezone

    from app.services.auth_service.models.user_auth import UserAuth
    from app.services.workspace_service.models import Workspace, WorkspaceRuntime
    from app.services.workspace_service.models.enums import WorkspaceStatus

    import uuid

    u = UserAuth(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@spread.test",
        password_hash="x",
        created_at=datetime.now(timezone.utc),
    )
    session.add(u)
    session.flush()
    ws = Workspace(
        name=f"ws_{uuid.uuid4().hex[:6]}",
        owner_user_id=u.user_auth_id,
        status=WorkspaceStatus.RUNNING.value,
        is_private=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(ws)
    session.flush()
    rt = WorkspaceRuntime(
        workspace_id=ws.workspace_id,
        node_id=node_key,
        reserved_cpu=cpu,
        reserved_memory_mb=mem,
        reserved_disk_mb=disk,
    )
    session.add(rt)
    session.commit()


def _seed_stopped_workload(session: Session, *, node_key: str) -> None:
    """Seed a STOPPED workspace — should NOT count toward active workload spread."""
    from datetime import datetime, timezone

    import uuid

    from app.services.auth_service.models.user_auth import UserAuth
    from app.services.workspace_service.models import Workspace, WorkspaceRuntime
    from app.services.workspace_service.models.enums import WorkspaceStatus

    u = UserAuth(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@spread.test",
        password_hash="x",
        created_at=datetime.now(timezone.utc),
    )
    session.add(u)
    session.flush()
    ws = Workspace(
        name=f"ws_{uuid.uuid4().hex[:6]}",
        owner_user_id=u.user_auth_id,
        status=WorkspaceStatus.STOPPED.value,
        is_private=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(ws)
    session.flush()
    rt = WorkspaceRuntime(
        workspace_id=ws.workspace_id,
        node_id=node_key,
        reserved_cpu=0.0,
        reserved_memory_mb=0,
        reserved_disk_mb=0,
    )
    session.add(rt)
    session.commit()


def test_equal_capacity_fewer_workloads_wins(policy_session: Session) -> None:
    """With equal free capacity, the node with fewer active workloads ranks first (anti-concentration)."""
    n_busy = _node(key="busy", alloc_cpu=4.0, alloc_mem=4096)
    n_idle = _node(key="idle", alloc_cpu=4.0, alloc_mem=4096)
    policy_session.add(n_busy)
    policy_session.add(n_idle)
    policy_session.commit()

    # Two active workloads on 'busy', zero on 'idle'
    _seed_active_workload(policy_session, node_key="busy", cpu=0.1, mem=64)
    _seed_active_workload(policy_session, node_key="busy", cpu=0.1, mem=64)

    req = WorkspaceComputeRequest(requested_cpu=0.5, requested_memory_mb=256, requested_disk_mb=1024)
    ranked = rank_candidate_nodes(policy_session, [n_busy, n_idle], req)

    assert len(ranked) == 2
    assert ranked[0].node_key == "idle", "idle node (fewer workloads) should rank first"
    assert ranked[1].node_key == "busy"


def test_capacity_primary_over_workload_spread(policy_session: Session) -> None:
    """Capacity-first: a node with much more free CPU beats one with fewer workloads."""
    n_large = _node(key="large", alloc_cpu=8.0, alloc_mem=8192)
    n_small = _node(key="small", alloc_cpu=4.0, alloc_mem=4096)
    policy_session.add(n_large)
    policy_session.add(n_small)
    policy_session.commit()

    # 3 workloads on large (still has 6.5 CPU free), 0 on small (4.0 CPU free)
    for _ in range(3):
        _seed_active_workload(policy_session, node_key="large", cpu=0.5, mem=512)

    req = WorkspaceComputeRequest(requested_cpu=0.5, requested_memory_mb=256, requested_disk_mb=1024)
    ranked = rank_candidate_nodes(policy_session, [n_large, n_small], req)

    assert len(ranked) == 2
    # large has 6.5 CPU free vs small's 4.0 — capacity wins even though large has more workloads
    assert ranked[0].node_key == "large"


def test_stopped_workloads_not_counted_for_spread(policy_session: Session) -> None:
    """STOPPED workspaces must not influence the active-workload spread ordering."""
    n1 = _node(key="n1", alloc_cpu=4.0, alloc_mem=4096)
    n2 = _node(key="n2", alloc_cpu=4.0, alloc_mem=4096)
    policy_session.add(n1)
    policy_session.add(n2)
    policy_session.commit()

    # 5 stopped workloads on n1 — these must not count
    for _ in range(5):
        _seed_stopped_workload(policy_session, node_key="n1")

    req = WorkspaceComputeRequest(requested_cpu=0.5, requested_memory_mb=256, requested_disk_mb=1024)
    ranked = rank_candidate_nodes(policy_session, [n1, n2], req)

    assert len(ranked) == 2
    # Both have 0 active workloads; n1 < n2 lexicographically → n1 first
    assert ranked[0].node_key == "n1"


def test_scheduling_sort_key_spread_deterministic() -> None:
    """scheduling_sort_key_spread produces a stable, deterministic tuple."""
    from app.services.scheduler_service.policy import scheduling_sort_key_spread

    k1 = scheduling_sort_key_spread(4.0, 4096, 2, "node-a")
    k2 = scheduling_sort_key_spread(4.0, 4096, 1, "node-b")
    # node-b has fewer workloads → smaller tuple → ranks first (sorts before node-a)
    assert k2 < k1


def test_scheduling_sort_key_spread_capacity_beats_workload_count() -> None:
    """Higher effective CPU is primary even when workload_count is larger."""
    from app.services.scheduler_service.policy import scheduling_sort_key_spread

    k_capacity = scheduling_sort_key_spread(8.0, 8192, 5, "node-heavy")
    k_spread = scheduling_sort_key_spread(2.0, 2048, 0, "node-light")
    # node-heavy has more free CPU → should sort before node-light despite 5 workloads
    assert k_capacity < k_spread
