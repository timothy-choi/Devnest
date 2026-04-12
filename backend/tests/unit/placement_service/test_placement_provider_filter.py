"""Placement pool filter: ``DEVNEST_NODE_PROVIDER`` (local | ec2 | all)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
    ExecutionNodeStatus,
)
from app.services.placement_service.node_placement import select_node_for_workspace


@pytest.fixture
def placement_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _add_node(session: Session, *, key: str, provider: str, cpu: float = 4.0, mem: int = 8192) -> None:
    total_cpu = max(4.0, cpu)
    total_mem = max(8192, mem)
    session.add(
        ExecutionNode(
            node_key=key,
            name=key,
            provider_type=provider,
            status=ExecutionNodeStatus.READY.value,
            schedulable=True,
            execution_mode=ExecutionNodeExecutionMode.LOCAL_DOCKER.value,
            total_cpu=total_cpu,
            total_memory_mb=total_mem,
            allocatable_cpu=cpu,
            allocatable_memory_mb=mem,
        ),
    )
    session.commit()


def test_placement_all_selects_either_provider(placement_engine, monkeypatch) -> None:
    def _settings():
        m = MagicMock()
        m.devnest_node_provider = "all"
        return m

    monkeypatch.setattr(
        "app.services.placement_service.node_placement.get_settings",
        _settings,
    )
    with Session(placement_engine) as session:
        _add_node(session, key="local-a", provider=ExecutionNodeProviderType.LOCAL.value, cpu=8.0)
        _add_node(session, key="ec2-b", provider=ExecutionNodeProviderType.EC2.value, cpu=6.0)
        picked = select_node_for_workspace(session, workspace_id=1)
        assert picked.node_key == "local-a"


def test_placement_local_excludes_ec2(placement_engine, monkeypatch) -> None:
    def _settings():
        m = MagicMock()
        m.devnest_node_provider = "local"
        return m

    monkeypatch.setattr(
        "app.services.placement_service.node_placement.get_settings",
        _settings,
    )
    with Session(placement_engine) as session:
        _add_node(session, key="ec2-only", provider=ExecutionNodeProviderType.EC2.value, cpu=8.0)
        _add_node(session, key="local-only", provider=ExecutionNodeProviderType.LOCAL.value, cpu=4.0)
        picked = select_node_for_workspace(session, workspace_id=1)
        assert picked.node_key == "local-only"


def test_placement_local_includes_unspecified_provider(placement_engine, monkeypatch) -> None:
    def _settings():
        m = MagicMock()
        m.devnest_node_provider = "local"
        return m

    monkeypatch.setattr(
        "app.services.placement_service.node_placement.get_settings",
        _settings,
    )
    with Session(placement_engine) as session:
        _add_node(session, key="unspec", provider=ExecutionNodeProviderType.UNSPECIFIED.value, cpu=8.0)
        _add_node(session, key="ec2-x", provider=ExecutionNodeProviderType.EC2.value, cpu=8.0)
        picked = select_node_for_workspace(session, workspace_id=1)
        assert picked.node_key == "unspec"


def test_placement_ec2_excludes_local(placement_engine, monkeypatch) -> None:
    def _settings():
        m = MagicMock()
        m.devnest_node_provider = "ec2"
        return m

    monkeypatch.setattr(
        "app.services.placement_service.node_placement.get_settings",
        _settings,
    )
    with Session(placement_engine) as session:
        _add_node(session, key="local-only", provider=ExecutionNodeProviderType.LOCAL.value, cpu=8.0)
        _add_node(session, key="ec2-only", provider=ExecutionNodeProviderType.EC2.value, cpu=4.0)
        picked = select_node_for_workspace(session, workspace_id=1)
        assert picked.node_key == "ec2-only"
