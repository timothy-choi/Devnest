"""Unit tests: EC2 host disk/memory gates for placement (resource-aware scheduling)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.libs.common.config import get_settings
from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeProviderType,
    ExecutionNodeResourceStatus,
    ExecutionNodeStatus,
)
from app.services.placement_service.errors import NoSchedulableNodeError
from app.services.placement_service.node_placement import select_node_for_workspace
from app.services.placement_service.node_resource_monitor import _apply_resource_metrics_to_node


@pytest.fixture(autouse=True)
def _clear_settings_cache_after_host_resource_test() -> None:
    yield
    get_settings.cache_clear()


@pytest.fixture
def host_resource_placement_engine(monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setenv("DEVNEST_NODE_PROVIDER", "ec2")
    monkeypatch.setenv("DEVNEST_NODE_RESOURCE_MONITOR_ENABLED", "true")
    monkeypatch.setenv("DEVNEST_ENABLE_MULTI_NODE_SCHEDULING", "true")
    monkeypatch.setenv("DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT", "false")
    monkeypatch.setenv("DEVNEST_NODE_MIN_FREE_DISK_MB", "10240")
    monkeypatch.setenv("DEVNEST_NODE_MIN_FREE_MEMORY_MB", "1024")
    monkeypatch.setenv("DEVNEST_NODE_RESOURCE_CHECK_INTERVAL_SECONDS", "60")
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    get_settings.cache_clear()


def _add_ec2_node(
    session: Session,
    *,
    key: str,
    disk_free_mb: int,
    memory_free_mb: int,
    last_check_at: datetime,
    resource_status: str | None = None,
    schedulable: bool = True,
    alloc_cpu: float = 8.0,
) -> ExecutionNode:
    n = ExecutionNode(
        node_key=key,
        name=key,
        provider_type=ExecutionNodeProviderType.EC2.value,
        status=ExecutionNodeStatus.READY.value,
        schedulable=schedulable,
        total_cpu=max(4.0, float(alloc_cpu)),
        total_memory_mb=8192,
        allocatable_cpu=alloc_cpu,
        allocatable_memory_mb=8192,
        allocatable_disk_mb=102_400,
        max_workspaces=8,
        disk_free_mb=disk_free_mb,
        memory_free_mb=memory_free_mb,
        last_resource_check_at=last_check_at,
        resource_status=resource_status,
    )
    session.add(n)
    session.commit()
    session.refresh(n)
    return n


def test_select_skips_ec2_low_disk_prefers_healthy_node(host_resource_placement_engine: Engine) -> None:
    now = datetime.now(timezone.utc)
    with Session(host_resource_placement_engine) as session:
        _add_ec2_node(session, key="low-disk", disk_free_mb=2048, memory_free_mb=8192, last_check_at=now)
        _add_ec2_node(session, key="healthy", disk_free_mb=50_000, memory_free_mb=8192, last_check_at=now, alloc_cpu=4.0)
        picked = select_node_for_workspace(session, workspace_id=1)
        assert picked.node_key == "healthy"


def test_select_skips_ec2_when_resource_status_low_disk_even_if_counters_high(
    host_resource_placement_engine: Engine,
) -> None:
    now = datetime.now(timezone.utc)
    with Session(host_resource_placement_engine) as session:
        _add_ec2_node(
            session,
            key="flagged-low-disk",
            disk_free_mb=80_000,
            memory_free_mb=8192,
            last_check_at=now,
            resource_status=ExecutionNodeResourceStatus.LOW_DISK.value,
        )
        _add_ec2_node(
            session,
            key="healthy-b",
            disk_free_mb=50_000,
            memory_free_mb=8192,
            last_check_at=now,
            alloc_cpu=4.0,
        )
        picked = select_node_for_workspace(session, workspace_id=1)
        assert picked.node_key == "healthy-b"


def test_select_skips_ec2_low_memory_prefers_healthy_node(host_resource_placement_engine: Engine) -> None:
    now = datetime.now(timezone.utc)
    with Session(host_resource_placement_engine) as session:
        _add_ec2_node(session, key="low-mem", disk_free_mb=50_000, memory_free_mb=256, last_check_at=now)
        _add_ec2_node(session, key="ok-mem", disk_free_mb=50_000, memory_free_mb=4096, last_check_at=now, alloc_cpu=4.0)
        picked = select_node_for_workspace(session, workspace_id=1)
        assert picked.node_key == "ok-mem"


def test_select_skips_ec2_stale_resource_check(host_resource_placement_engine: Engine) -> None:
    now = datetime.now(timezone.utc)
    stale_at = now - timedelta(seconds=400)
    with Session(host_resource_placement_engine) as session:
        _add_ec2_node(
            session,
            key="stale-check",
            disk_free_mb=50_000,
            memory_free_mb=8192,
            last_check_at=stale_at,
        )
        _add_ec2_node(
            session,
            key="fresh-check",
            disk_free_mb=50_000,
            memory_free_mb=8192,
            last_check_at=now,
            alloc_cpu=4.0,
        )
        picked = select_node_for_workspace(session, workspace_id=1)
        assert picked.node_key == "fresh-check"


def test_resource_recovery_restores_schedulable_when_metrics_normalize(host_resource_placement_engine: Engine) -> None:
    now = datetime.now(timezone.utc)
    with Session(host_resource_placement_engine) as session:
        n = _add_ec2_node(
            session,
            key="recover",
            disk_free_mb=1000,
            memory_free_mb=8192,
            last_check_at=now,
            resource_status=ExecutionNodeResourceStatus.LOW_DISK.value,
            schedulable=False,
        )
        _apply_resource_metrics_to_node(
            session,
            n,
            {
                "disk_total_mb": 100000,
                "disk_free_mb": 50000,
                "memory_total_mb": 8192,
                "memory_free_mb": 4096,
            },
            docker_note=None,
        )
        session.commit()
        session.refresh(n)
        assert n.schedulable is True
        assert (n.resource_status or "").upper() == ExecutionNodeResourceStatus.OK.value
        assert n.resource_warning_message is None


def test_resource_recovery_does_not_set_schedulable_when_heartbeat_gate_unmet(
    host_resource_placement_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT", "true")
    monkeypatch.setenv("DEVNEST_NODE_HEARTBEAT_MAX_AGE_SECONDS", "120")
    get_settings.cache_clear()
    now = datetime.now(timezone.utc)
    with Session(host_resource_placement_engine) as session:
        n = _add_ec2_node(
            session,
            key="recover-hb",
            disk_free_mb=1000,
            memory_free_mb=8192,
            last_check_at=now,
            resource_status=ExecutionNodeResourceStatus.LOW_DISK.value,
            schedulable=False,
        )
        n.last_heartbeat_at = now - timedelta(seconds=9999)
        session.add(n)
        session.commit()
        _apply_resource_metrics_to_node(
            session,
            n,
            {
                "disk_total_mb": 100000,
                "disk_free_mb": 50000,
                "memory_total_mb": 8192,
                "memory_free_mb": 4096,
            },
            docker_note=None,
        )
        session.commit()
        session.refresh(n)
        assert n.schedulable is False
        assert (n.resource_status or "").upper() == ExecutionNodeResourceStatus.OK.value


def test_scheduler_skip_logs_low_disk_event(host_resource_placement_engine: Engine) -> None:
    now = datetime.now(timezone.utc)
    with Session(host_resource_placement_engine) as session:
        _add_ec2_node(session, key="only-low-disk", disk_free_mb=2048, memory_free_mb=8192, last_check_at=now)
        from app.libs.observability.log_events import LogEvent

        with patch("app.services.placement_service.host_resource.log_event") as mock_log:
            with pytest.raises(NoSchedulableNodeError):
                select_node_for_workspace(session, workspace_id=1)
        codes = [c.args[1] for c in mock_log.call_args_list if len(c.args) > 1]
        assert LogEvent.SCHEDULER_NODE_SKIPPED_LOW_DISK in codes
