"""Unit tests: execution node placement selection (SQLite)."""

from __future__ import annotations

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.libs.common.config import get_settings
from app.services.placement_service.errors import (
    ExecutionNodeNotFoundError,
    InvalidPlacementParametersError,
    NoSchedulableNodeError,
)
from app.services.placement_service.models import ExecutionNode, ExecutionNodeProviderType, ExecutionNodeStatus
from app.services.placement_service.node_placement import (
    get_node,
    list_schedulable_nodes,
    reserve_node_for_workspace,
    select_node_for_workspace,
)


@pytest.fixture(autouse=True)
def _refresh_get_settings_cache_after_each_placement_test() -> None:
    yield
    get_settings.cache_clear()


@pytest.fixture
def placement_engine() -> Engine:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _add_node(
    session: Session,
    *,
    key: str,
    schedulable: bool = True,
    status: str = ExecutionNodeStatus.READY.value,
    alloc_cpu: float = 4.0,
    alloc_mem: int = 8192,
    alloc_disk: int = 102_400,
    max_workspaces: int = 32,
) -> ExecutionNode:
    total_cpu = max(4.0, float(alloc_cpu))
    total_memory_mb = max(8192, int(alloc_mem))
    n = ExecutionNode(
        node_key=key,
        name=key,
        provider_type=ExecutionNodeProviderType.LOCAL.value,
        status=status,
        schedulable=schedulable,
        total_cpu=total_cpu,
        total_memory_mb=total_memory_mb,
        allocatable_cpu=alloc_cpu,
        allocatable_memory_mb=alloc_mem,
        allocatable_disk_mb=alloc_disk,
        max_workspaces=max_workspaces,
    )
    session.add(n)
    session.commit()
    session.refresh(n)
    return n


def test_list_schedulable_nodes_filters_status_and_flag(placement_engine: Engine) -> None:
    with Session(placement_engine) as session:
        _add_node(session, key="a")
        _add_node(session, key="b", schedulable=False)
        _add_node(session, key="c", status=ExecutionNodeStatus.DRAINING.value)
        rows = list_schedulable_nodes(session)
        assert [r.node_key for r in rows] == ["a"]


def test_list_schedulable_nodes_single_node_gate_excludes_secondary(
    placement_engine: Engine,
    disable_multi_node_scheduling: None,
) -> None:
    with Session(placement_engine) as session:
        _add_node(session, key="primary-row")
        _add_node(session, key="secondary-row")
        rows = list_schedulable_nodes(session)
        assert len(rows) == 1
        assert rows[0].node_key == "primary-row"


def test_list_schedulable_nodes_includes_all_ready_when_multi_node_enabled(
    placement_engine: Engine,
    enable_multi_node_scheduling: None,
) -> None:
    """With flag true: both READY+schedulable nodes appear (sorted by node_key)."""
    with Session(placement_engine) as session:
        _add_node(session, key="node-b")
        _add_node(session, key="node-a")
        rows = list_schedulable_nodes(session)
        assert [r.node_key for r in rows] == ["node-a", "node-b"]


def test_select_prefers_primary_by_id_when_multi_node_disabled(
    placement_engine: Engine,
    disable_multi_node_scheduling: None,
) -> None:
    with Session(placement_engine) as session:
        _add_node(session, key="weaker-first", alloc_cpu=2.0)
        _add_node(session, key="stronger-second", alloc_cpu=16.0)
        picked = select_node_for_workspace(session, workspace_id=1)
        assert picked.node_key == "weaker-first"


def test_step7_default_multi_node_off_primary_wins_over_higher_cpu_secondary(
    placement_engine: Engine,
) -> None:
    """Without env override, app default is primary-only (lower id) even if secondary has more CPU."""
    with Session(placement_engine) as session:
        _add_node(session, key="node-1", alloc_cpu=4.0)
        _add_node(session, key="node-2", alloc_cpu=16.0)
        picked = select_node_for_workspace(session, workspace_id=1)
        assert picked.node_key == "node-1"


def test_step7_multi_node_on_can_select_secondary_when_it_ranks_first(
    placement_engine: Engine,
    enable_multi_node_scheduling: None,
) -> None:
    with Session(placement_engine) as session:
        _add_node(session, key="node-1", alloc_cpu=4.0)
        _add_node(session, key="node-2", alloc_cpu=16.0)
        picked = select_node_for_workspace(session, workspace_id=1)
        assert picked.node_key == "node-2"


def test_select_node_deterministic_highest_allocatable_cpu(
    placement_engine: Engine,
    enable_multi_node_scheduling: None,
) -> None:
    with Session(placement_engine) as session:
        _add_node(session, key="low", alloc_cpu=2.0, alloc_mem=4096)
        _add_node(session, key="high", alloc_cpu=8.0, alloc_mem=4096)
        _add_node(session, key="mid", alloc_cpu=6.0, alloc_mem=4096)
        picked = select_node_for_workspace(session, workspace_id=1)
        assert picked.node_key == "high"


def test_select_node_tie_breaker_node_key(
    placement_engine: Engine,
    enable_multi_node_scheduling: None,
) -> None:
    with Session(placement_engine) as session:
        _add_node(session, key="z-node", alloc_cpu=4.0)
        _add_node(session, key="a-node", alloc_cpu=4.0)
        picked = select_node_for_workspace(session, workspace_id=99)
        assert picked.node_key == "a-node"


def test_select_prefers_node_with_fewer_active_workloads_when_capacity_equal(
    placement_engine: Engine,
    enable_multi_node_scheduling: None,
) -> None:
    """Equal allocatable CPU/memory → active_workload_count asc prefers the less-loaded node."""
    from app.services.auth_service.models.user_auth import UserAuth
    from app.services.workspace_service.models import Workspace, WorkspaceRuntime
    from app.services.workspace_service.models.enums import WorkspaceStatus

    with Session(placement_engine) as session:
        n_a = _add_node(session, key="node-a-spread", alloc_cpu=8.0, alloc_mem=8192)
        _add_node(session, key="node-z-spread", alloc_cpu=8.0, alloc_mem=8192)
        u = UserAuth(username="spr1", password_hash="x", email="spr1@e.com")
        session.add(u)
        session.commit()
        session.refresh(u)
        ws = Workspace(
            name="on-node-a",
            owner_user_id=u.user_auth_id,
            status=WorkspaceStatus.RUNNING.value,
            execution_node_id=int(n_a.id),
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
        session.add(
            WorkspaceRuntime(
                workspace_id=ws.workspace_id,
                node_id="node-a-spread",
                reserved_cpu=0.5,
                reserved_memory_mb=512,
                reserved_disk_mb=1024,
            ),
        )
        session.commit()
        picked = select_node_for_workspace(session, workspace_id=777)
        assert picked.node_key == "node-z-spread"


def test_select_node_no_node_raises(placement_engine: Engine) -> None:
    with Session(placement_engine) as session:
        with pytest.raises(NoSchedulableNodeError):
            select_node_for_workspace(session, workspace_id=1)


def test_select_node_rejects_non_positive_request(placement_engine: Engine) -> None:
    with Session(placement_engine) as session:
        _add_node(session, key="n", alloc_cpu=4.0)
        with pytest.raises(InvalidPlacementParametersError):
            select_node_for_workspace(
                session,
                workspace_id=1,
                requested_cpu=0.0,
                requested_memory_mb=512,
                requested_disk_mb=4096,
            )
        with pytest.raises(InvalidPlacementParametersError):
            select_node_for_workspace(
                session,
                workspace_id=1,
                requested_cpu=1.0,
                requested_memory_mb=0,
                requested_disk_mb=4096,
            )
        with pytest.raises(InvalidPlacementParametersError):
            select_node_for_workspace(
                session,
                workspace_id=1,
                requested_cpu=1.0,
                requested_memory_mb=512,
                requested_disk_mb=0,
            )


def test_select_node_insufficient_capacity(placement_engine: Engine) -> None:
    with Session(placement_engine) as session:
        _add_node(session, key="tiny", alloc_cpu=0.5, alloc_mem=128)
        with pytest.raises(NoSchedulableNodeError):
            select_node_for_workspace(
                session,
                workspace_id=1,
                requested_cpu=1.0,
                requested_memory_mb=512,
                requested_disk_mb=4096,
            )


def test_select_node_repairs_missing_default_topology(placement_engine: Engine) -> None:
    with Session(placement_engine) as session:
        n = _add_node(session, key="missing-topology")
        n.default_topology_id = None
        session.add(n)
        session.commit()

        picked = select_node_for_workspace(session, workspace_id=1)
        session.refresh(picked)

        assert picked.node_key == "missing-topology"
        assert picked.default_topology_id == 1


def test_select_node_stale_heartbeat_still_schedulable_when_gating_off(placement_engine: Engine) -> None:
    from datetime import datetime, timedelta, timezone

    stale = datetime.now(timezone.utc) - timedelta(days=1)
    with Session(placement_engine) as session:
        n = _add_node(session, key="stale")
        n.last_heartbeat_at = stale
        session.add(n)
        session.commit()
        picked = select_node_for_workspace(session, workspace_id=1)
        assert picked.node_key == "stale"


def test_select_node_require_fresh_heartbeat_excludes_stale(monkeypatch: pytest.MonkeyPatch, placement_engine: Engine) -> None:
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT", "true")
    monkeypatch.setenv("DEVNEST_NODE_HEARTBEAT_MAX_AGE_SECONDS", "60")
    get_settings.cache_clear()
    stale = datetime.now(timezone.utc) - timedelta(seconds=120)
    with Session(placement_engine) as session:
        n = _add_node(session, key="stale")
        n.last_heartbeat_at = stale
        session.add(n)
        session.commit()
        with pytest.raises(NoSchedulableNodeError):
            select_node_for_workspace(session, workspace_id=1)


def test_select_node_require_fresh_heartbeat_allows_recent(monkeypatch: pytest.MonkeyPatch, placement_engine: Engine) -> None:
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT", "true")
    monkeypatch.setenv("DEVNEST_NODE_HEARTBEAT_MAX_AGE_SECONDS", "60")
    get_settings.cache_clear()
    fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
    with Session(placement_engine) as session:
        n = _add_node(session, key="fresh")
        n.last_heartbeat_at = fresh
        session.add(n)
        session.commit()
        picked = select_node_for_workspace(session, workspace_id=1)
        assert picked.node_key == "fresh"


def test_get_node_by_key_and_id(placement_engine: Engine) -> None:
    with Session(placement_engine) as session:
        n = _add_node(session, key="k1")
        by_key = get_node(session, node_key="k1")
        assert by_key.id == n.id
        by_id = get_node(session, node_id=n.id)
        assert by_id.node_key == "k1"


def test_get_node_missing_raises(placement_engine: Engine) -> None:
    with Session(placement_engine) as session:
        with pytest.raises(ExecutionNodeNotFoundError):
            get_node(session, node_key="nope")


def test_reserve_node_matches_select(placement_engine: Engine) -> None:
    with Session(placement_engine) as session:
        _add_node(session, key="x", alloc_cpu=4.0)
        reserved = reserve_node_for_workspace(session, workspace_id=1)
        selected = select_node_for_workspace(session, workspace_id=1)
        assert reserved.node_key == selected.node_key == "x"


def test_select_skips_node_when_effective_capacity_exhausted(placement_engine: Engine) -> None:
    from app.services.auth_service.models.user_auth import UserAuth
    from app.services.workspace_service.models import Workspace, WorkspaceRuntime
    from app.services.workspace_service.models.enums import WorkspaceStatus

    with Session(placement_engine) as session:
        n_only = _add_node(session, key="only", alloc_cpu=2.0, alloc_mem=2048)
        u = UserAuth(username="p1", password_hash="x", email="p1@e.com")
        session.add(u)
        session.commit()
        session.refresh(u)
        ws = Workspace(
            name="pw",
            owner_user_id=u.user_auth_id,
            status=WorkspaceStatus.RUNNING.value,
            execution_node_id=int(n_only.id),
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
        session.add(
            WorkspaceRuntime(
                workspace_id=ws.workspace_id,
                node_id="only",
                reserved_cpu=1.5,
                reserved_memory_mb=1536,
                reserved_disk_mb=8192,
            ),
        )
        session.commit()
        with pytest.raises(NoSchedulableNodeError):
            select_node_for_workspace(
                session,
                workspace_id=99,
                requested_cpu=1.0,
                requested_memory_mb=1024,
                requested_disk_mb=4096,
            )
        picked = select_node_for_workspace(
            session,
            workspace_id=100,
            requested_cpu=0.25,
            requested_memory_mb=256,
            requested_disk_mb=1024,
        )
        assert picked.node_key == "only"


def test_select_allows_placement_when_only_error_workloads_have_ledger(placement_engine: Engine) -> None:
    """ERROR workspaces do not consume effective capacity; stale ledger must not block others."""
    from app.services.auth_service.models.user_auth import UserAuth
    from app.services.workspace_service.models import Workspace, WorkspaceRuntime
    from app.services.workspace_service.models.enums import WorkspaceStatus

    with Session(placement_engine) as session:
        _add_node(session, key="solo", alloc_cpu=2.0, alloc_mem=2048)
        u = UserAuth(username="pe1", password_hash="x", email="pe1@e.com")
        session.add(u)
        session.commit()
        session.refresh(u)
        node = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == "solo")).first()
        assert node is not None and node.id is not None
        ws = Workspace(
            name="err_ws",
            owner_user_id=u.user_auth_id,
            status=WorkspaceStatus.ERROR.value,
            execution_node_id=int(node.id),
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
        session.add(
            WorkspaceRuntime(
                workspace_id=ws.workspace_id,
                node_id="solo",
                reserved_cpu=1.9,
                reserved_memory_mb=2000,
                reserved_disk_mb=16_384,
            ),
        )
        session.commit()
        picked = select_node_for_workspace(
            session,
            workspace_id=501,
            requested_cpu=1.0,
            requested_memory_mb=512,
            requested_disk_mb=1024,
        )
        assert picked.node_key == "solo"


def test_select_node_rejects_when_max_workspaces_reached(placement_engine: Engine) -> None:
    from app.services.auth_service.models.user_auth import UserAuth
    from app.services.workspace_service.models import Workspace, WorkspaceRuntime
    from app.services.workspace_service.models.enums import WorkspaceStatus

    with Session(placement_engine) as session:
        n = _add_node(session, key="slot-full", max_workspaces=1)
        u = UserAuth(username="slot1", password_hash="x", email="slot1@e.com")
        session.add(u)
        session.commit()
        session.refresh(u)
        ws = Workspace(
            name="slot-ws",
            owner_user_id=u.user_auth_id,
            status=WorkspaceStatus.RUNNING.value,
            execution_node_id=int(n.id),
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
        session.add(
            WorkspaceRuntime(
                workspace_id=ws.workspace_id,
                node_id="slot-full",
                reserved_cpu=0.5,
                reserved_memory_mb=256,
                reserved_disk_mb=1024,
            ),
        )
        session.commit()

        with pytest.raises(NoSchedulableNodeError, match="max_workspaces"):
            select_node_for_workspace(
                session,
                workspace_id=999,
                requested_cpu=0.5,
                requested_memory_mb=256,
                requested_disk_mb=1024,
            )


def test_select_node_rejects_when_free_disk_insufficient(placement_engine: Engine) -> None:
    with Session(placement_engine) as session:
        _add_node(session, key="disk-tight", alloc_disk=2048)
        with pytest.raises(NoSchedulableNodeError, match="effective_free_disk_mb"):
            select_node_for_workspace(
                session,
                workspace_id=1,
                requested_cpu=0.5,
                requested_memory_mb=256,
                requested_disk_mb=4096,
            )


def test_select_node_chooses_other_node_when_first_is_full(
    placement_engine: Engine,
    enable_multi_node_scheduling: None,
) -> None:
    from app.services.auth_service.models.user_auth import UserAuth
    from app.services.workspace_service.models import Workspace, WorkspaceRuntime
    from app.services.workspace_service.models.enums import WorkspaceStatus

    with Session(placement_engine) as session:
        n_full = _add_node(session, key="full", max_workspaces=1, alloc_disk=8192)
        _add_node(session, key="roomy", max_workspaces=4, alloc_disk=8192)
        u = UserAuth(username="other1", password_hash="x", email="other1@e.com")
        session.add(u)
        session.commit()
        session.refresh(u)
        ws = Workspace(
            name="full-ws",
            owner_user_id=u.user_auth_id,
            status=WorkspaceStatus.RUNNING.value,
            execution_node_id=int(n_full.id),
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
        session.add(
            WorkspaceRuntime(
                workspace_id=ws.workspace_id,
                node_id="full",
                reserved_cpu=0.5,
                reserved_memory_mb=256,
                reserved_disk_mb=1024,
            ),
        )
        session.commit()

        picked = select_node_for_workspace(
            session,
            workspace_id=123,
            requested_cpu=0.5,
            requested_memory_mb=256,
            requested_disk_mb=1024,
        )
        assert picked.node_key == "roomy"


def test_drained_node_not_in_pool_remaining_node_receives_placement(
    placement_engine: Engine,
    enable_multi_node_scheduling: None,
) -> None:
    """Non-schedulable (drained) nodes are excluded; placement lands on the other READY node (Step 11)."""
    with Session(placement_engine) as session:
        _add_node(session, key="alpha", alloc_cpu=8.0)
        _add_node(session, key="zeta", alloc_cpu=16.0, schedulable=False)
        picked = select_node_for_workspace(session, workspace_id=1)
        assert picked.node_key == "alpha"


def test_select_node_reports_no_capacity_when_all_nodes_ineligible(
    placement_engine: Engine,
    enable_multi_node_scheduling: None,
) -> None:
    from app.services.auth_service.models.user_auth import UserAuth
    from app.services.workspace_service.models import Workspace, WorkspaceRuntime
    from app.services.workspace_service.models.enums import WorkspaceStatus

    with Session(placement_engine) as session:
        n_slot = _add_node(session, key="slot-full", max_workspaces=1, alloc_disk=8192)
        _add_node(session, key="disk-tight", max_workspaces=4, alloc_disk=1024)
        u = UserAuth(username="none1", password_hash="x", email="none1@e.com")
        session.add(u)
        session.commit()
        session.refresh(u)
        ws = Workspace(
            name="slot-ws",
            owner_user_id=u.user_auth_id,
            status=WorkspaceStatus.RUNNING.value,
            execution_node_id=int(n_slot.id),
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
        session.add(
            WorkspaceRuntime(
                workspace_id=ws.workspace_id,
                node_id="slot-full",
                reserved_cpu=0.5,
                reserved_memory_mb=256,
                reserved_disk_mb=1024,
            ),
        )
        session.commit()

        with pytest.raises(NoSchedulableNodeError, match="effective_free_disk_mb"):
            select_node_for_workspace(
                session,
                workspace_id=777,
                requested_cpu=0.5,
                requested_memory_mb=256,
                requested_disk_mb=2048,
            )
