"""Unit tests: execution node placement selection (SQLite)."""

from __future__ import annotations

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

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


def test_select_node_deterministic_highest_allocatable_cpu(placement_engine: Engine) -> None:
    with Session(placement_engine) as session:
        _add_node(session, key="low", alloc_cpu=2.0, alloc_mem=4096)
        _add_node(session, key="high", alloc_cpu=8.0, alloc_mem=4096)
        _add_node(session, key="mid", alloc_cpu=6.0, alloc_mem=4096)
        picked = select_node_for_workspace(session, workspace_id=1)
        assert picked.node_key == "high"


def test_select_node_tie_breaker_node_key(placement_engine: Engine) -> None:
    with Session(placement_engine) as session:
        _add_node(session, key="z-node", alloc_cpu=4.0)
        _add_node(session, key="a-node", alloc_cpu=4.0)
        picked = select_node_for_workspace(session, workspace_id=99)
        assert picked.node_key == "a-node"


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
            )
        with pytest.raises(InvalidPlacementParametersError):
            select_node_for_workspace(
                session,
                workspace_id=1,
                requested_cpu=1.0,
                requested_memory_mb=0,
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
            )


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
