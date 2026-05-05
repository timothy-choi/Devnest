"""Unit tests: autoscaler decisions (mocked settings / counts / EC2 request)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, select
from sqlmodel import SQLModel, create_engine

from app.services.autoscaler_service.service import (
    ec2_autoscaler_provisioning_config_errors,
    evaluate_fleet_autoscaler_tick,
    evaluate_scale_down,
    evaluate_scale_up,
    execute_scale_down,
    maybe_provision_on_no_schedulable_capacity,
    record_placement_failed_scale_out_signal,
    reclaim_one_idle_ec2_node,
    run_scale_out_tick,
)
from app.services.auth_service.models import UserAuth
from app.services.placement_service.models import ExecutionNode, ExecutionNodeProviderType, ExecutionNodeStatus
from app.services.workspace_service.models import Workspace, WorkspaceJob, WorkspaceRuntime
from app.services.workspace_service.models.enums import WorkspaceJobStatus, WorkspaceJobType, WorkspaceStatus
from app.workers.autoscaler_loop import run_autoscaler_loop_tick, run_autoscaler_scale_down_tick

from app.libs.common.config import get_settings


def _scale_out_settings() -> SimpleNamespace:
    return SimpleNamespace(
        devnest_autoscaler_enabled=True,
        devnest_autoscaler_evaluate_only=False,
        devnest_autoscaler_min_nodes=1,
        devnest_autoscaler_max_nodes=5,
        devnest_autoscaler_min_idle_slots=1,
        devnest_autoscaler_max_concurrent_provisioning=3,
        devnest_autoscaler_scale_out_cooldown_seconds=0,
        devnest_autoscaler_scale_in_cooldown_seconds=0,
        devnest_enable_multi_node_scheduling=True,
        devnest_node_provider="all",
        devnest_require_fresh_node_heartbeat=False,
        aws_region="us-east-1",
        devnest_ec2_ami_id="ami-12345678",
        devnest_ec2_instance_type="t3.micro",
        devnest_ec2_subnet_id="subnet-12345678",
        devnest_ec2_security_group_ids="sg-12345678",
        devnest_ec2_instance_profile="DevNestExecutionNodeProfile",
        devnest_ec2_key_name="",
        devnest_ec2_default_execution_mode="ssm_docker",
        devnest_ec2_bootstrap_prebaked=True,
        devnest_ec2_user_data="",
        devnest_ec2_user_data_b64="",
    )


def _scale_down_settings(
    *,
    min_nodes: int = 0,
    min_ec2_before_reclaim: int = 2,
    cooldown_seconds: int = 0,
    idle_seconds: int = 300,
) -> SimpleNamespace:
    return SimpleNamespace(
        devnest_autoscaler_enabled=True,
        devnest_autoscaler_evaluate_only=False,
        devnest_autoscaler_min_nodes=min_nodes,
        devnest_autoscaler_min_ec2_nodes_before_reclaim=min_ec2_before_reclaim,
        devnest_autoscaler_scale_in_cooldown_seconds=cooldown_seconds,
        devnest_autoscaler_scale_down_idle_seconds=idle_seconds,
    )


def _seed_ec2_node(
    session: Session,
    node_key: str,
    *,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    last_resource_check_at: datetime | None = None,
    status: str = ExecutionNodeStatus.READY.value,
    schedulable: bool = True,
) -> ExecutionNode:
    ts_created = created_at or (datetime.now(timezone.utc) - timedelta(seconds=600))
    ts_updated = updated_at if updated_at is not None else datetime.now(timezone.utc)
    node = ExecutionNode(
        node_key=node_key,
        name=node_key,
        provider_type=ExecutionNodeProviderType.EC2.value,
        provider_instance_id=f"i-{node_key.replace('-', '')[:16]:0<16}",
        status=status,
        schedulable=schedulable,
        total_cpu=2.0,
        total_memory_mb=4096,
        allocatable_cpu=2.0,
        allocatable_memory_mb=4096,
        created_at=ts_created,
        updated_at=ts_updated,
    )
    if last_resource_check_at is not None:
        node.last_resource_check_at = last_resource_check_at
    session.add(node)
    session.flush()
    return node


def _fake_terminate(session: Session, *, node_key: str, **_kw: object) -> ExecutionNode:
    node = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == node_key)).first()
    assert node is not None
    node.status = ExecutionNodeStatus.TERMINATED.value
    node.schedulable = False
    node.updated_at = datetime.now(timezone.utc)
    session.add(node)
    session.flush()
    return node


@pytest.fixture
def autoscaler_unit_engine(monkeypatch: pytest.MonkeyPatch) -> Engine:
    """Disable host-resource placement gate by default; predicates use real ``get_settings()``."""
    monkeypatch.setenv("DEVNEST_NODE_RESOURCE_MONITOR_ENABLED", "false")
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    get_settings.cache_clear()


@patch("app.services.autoscaler_service.service.count_ec2_provisioning_nodes", return_value=0)
@patch("app.services.autoscaler_service.service.get_settings")
def test_evaluate_scale_up_disabled(mock_settings: MagicMock, _prov: MagicMock) -> None:
    mock_settings.return_value = SimpleNamespace(devnest_autoscaler_enabled=False)
    ev = evaluate_scale_up(MagicMock(), insufficient_capacity=True)
    assert ev.should_provision is False
    assert "disabled" in ev.reason


@patch("app.services.autoscaler_service.service.count_ec2_provisioning_nodes", return_value=0)
@patch("app.services.autoscaler_service.service.get_settings")
def test_evaluate_scale_up_local_provider_skips(mock_settings: MagicMock, _prov: MagicMock) -> None:
    mock_settings.return_value = SimpleNamespace(
        devnest_autoscaler_enabled=True,
        devnest_node_provider="local",
        devnest_autoscaler_max_concurrent_provisioning=3,
    )
    ev = evaluate_scale_up(MagicMock(), insufficient_capacity=True)
    assert ev.should_provision is False
    assert "local" in ev.reason


@patch("app.services.autoscaler_service.service.count_ec2_provisioning_nodes", return_value=99)
@patch("app.services.autoscaler_service.service.get_settings")
def test_evaluate_scale_up_at_cap(mock_settings: MagicMock, _prov: MagicMock) -> None:
    mock_settings.return_value = SimpleNamespace(
        devnest_autoscaler_enabled=True,
        devnest_node_provider="ec2",
        devnest_autoscaler_max_concurrent_provisioning=3,
    )
    ev = evaluate_scale_up(MagicMock(), insufficient_capacity=True)
    assert ev.should_provision is False
    assert "cap" in ev.reason


@patch("app.services.autoscaler_service.service.count_ec2_provisioning_nodes", return_value=0)
@patch("app.services.autoscaler_service.service.get_settings")
@patch("app.services.autoscaler_service.service.Ec2ProvisionRequest")
def test_evaluate_scale_up_happy_path(
    mock_req_cls: MagicMock,
    mock_settings: MagicMock,
    _prov: MagicMock,
) -> None:
    mock_settings.return_value = SimpleNamespace(
        devnest_autoscaler_enabled=True,
        devnest_node_provider="ec2",
        devnest_autoscaler_max_concurrent_provisioning=3,
    )
    inst = MagicMock()
    mock_req_cls.from_settings.return_value = inst
    ev = evaluate_scale_up(MagicMock(), insufficient_capacity=True)
    assert ev.should_provision is True
    inst.validate.assert_called_once()


@patch("app.services.autoscaler_service.service.get_settings")
def test_maybe_provision_respects_flags(mock_settings: MagicMock) -> None:
    mock_settings.return_value = SimpleNamespace(
        devnest_autoscaler_enabled=False,
        devnest_autoscaler_provision_on_no_capacity=True,
    )
    assert maybe_provision_on_no_schedulable_capacity(MagicMock()) is None


@patch(
    "app.services.autoscaler_service.service._workload_counts_by_node_keys",
    return_value={"ec2-a": 0, "ec2-b": 0},
)
@patch("app.services.autoscaler_service.service._count_all_ready_schedulable_ec2", return_value=2)
@patch("app.services.autoscaler_service.service.get_settings")
def test_evaluate_scale_down_finds_idle(
    mock_settings: MagicMock,
    _n_ready: MagicMock,
    _counts: MagicMock,
) -> None:
    mock_settings.return_value = SimpleNamespace(devnest_autoscaler_min_ec2_nodes_before_reclaim=2)
    from app.services.placement_service.models import ExecutionNode, ExecutionNodeProviderType, ExecutionNodeStatus

    n1 = ExecutionNode(
        node_key="ec2-a",
        name="a",
        provider_type=ExecutionNodeProviderType.EC2.value,
        status=ExecutionNodeStatus.READY.value,
        schedulable=True,
        total_cpu=2.0,
        total_memory_mb=4096,
        allocatable_cpu=2.0,
        allocatable_memory_mb=4096,
    )
    n2 = ExecutionNode(
        node_key="ec2-b",
        name="b",
        provider_type=ExecutionNodeProviderType.EC2.value,
        status=ExecutionNodeStatus.READY.value,
        schedulable=True,
        total_cpu=2.0,
        total_memory_mb=4096,
        allocatable_cpu=2.0,
        allocatable_memory_mb=4096,
    )
    session = MagicMock()

    def _exec(stmt: object) -> MagicMock:
        m = MagicMock()

        def _all() -> list[ExecutionNode]:
            # Same order as SQL ``ORDER BY node_key ASC`` (``ec2-a`` before ``ec2-b``).
            return [n1, n2]

        m.all = _all
        return m

    session.exec.side_effect = _exec
    ev = evaluate_scale_down(session)
    assert ev.node_key == "ec2-a"
    assert ev.idle_ec2_ready_nodes == 2


@patch("app.services.autoscaler_service.service._count_all_ready_schedulable_ec2", return_value=1)
@patch("app.services.autoscaler_service.service.get_settings")
def test_evaluate_scale_down_refuses_below_min_ready(
    mock_settings: MagicMock,
    _n_ready: MagicMock,
) -> None:
    mock_settings.return_value = SimpleNamespace(devnest_autoscaler_min_ec2_nodes_before_reclaim=2)
    ev = evaluate_scale_down(MagicMock())
    assert ev.node_key is None
    assert "below minimum" in ev.reason
    assert ev.min_ec2_nodes_before_reclaim == 2


@patch("app.services.autoscaler_service.service._count_all_ready_schedulable_ec2", return_value=2)
@patch("app.services.autoscaler_service.service.get_settings")
def test_evaluate_scale_down_uses_configured_min_ready_exactly(
    mock_settings: MagicMock,
    _n_ready: MagicMock,
) -> None:
    """``min_ec2_nodes_before_reclaim`` is an explicit operator floor, including 0 or 1."""
    mock_settings.return_value = SimpleNamespace(devnest_autoscaler_min_ec2_nodes_before_reclaim=1)
    from app.services.placement_service.models import ExecutionNode, ExecutionNodeProviderType, ExecutionNodeStatus

    n1 = ExecutionNode(
        node_key="ec2-a",
        name="a",
        provider_type=ExecutionNodeProviderType.EC2.value,
        status=ExecutionNodeStatus.READY.value,
        schedulable=True,
        total_cpu=2.0,
        total_memory_mb=4096,
        allocatable_cpu=2.0,
        allocatable_memory_mb=4096,
    )
    n2 = ExecutionNode(
        node_key="ec2-b",
        name="b",
        provider_type=ExecutionNodeProviderType.EC2.value,
        status=ExecutionNodeStatus.READY.value,
        schedulable=True,
        total_cpu=2.0,
        total_memory_mb=4096,
        allocatable_cpu=2.0,
        allocatable_memory_mb=4096,
    )
    session = MagicMock()

    def _exec(_stmt: object) -> MagicMock:
        m = MagicMock()
        m.all = lambda: [n1, n2]
        return m

    session.exec.side_effect = _exec
    with patch(
        "app.services.autoscaler_service.service._workload_counts_by_node_keys",
        return_value={"ec2-a": 0, "ec2-b": 0},
    ):
        ev = evaluate_scale_down(session)
    assert ev.node_key == "ec2-a"
    assert ev.min_ec2_nodes_before_reclaim == 1
    assert "minimum before reclaim=1" in ev.reason


def test_evaluate_only_tick_recommends_scale_out_without_mutating_nodes(autoscaler_unit_engine) -> None:
    with Session(autoscaler_unit_engine) as session:
        user = UserAuth(username="auto", email="auto@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)
        node = ExecutionNode(
            node_key="ec2-full",
            name="ec2-full",
            provider_type=ExecutionNodeProviderType.EC2.value,
            status=ExecutionNodeStatus.READY.value,
            schedulable=True,
            total_cpu=2.0,
            total_memory_mb=4096,
            allocatable_cpu=2.0,
            allocatable_memory_mb=4096,
            max_workspaces=1,
        )
        session.add(node)
        session.commit()
        session.refresh(node)
        ws = Workspace(
            name="busy",
            owner_user_id=int(user.user_auth_id),
            status=WorkspaceStatus.RUNNING.value,
            execution_node_id=int(node.id),
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
        session.add(
            WorkspaceRuntime(
                workspace_id=int(ws.workspace_id),
                node_id="ec2-full",
                reserved_cpu=1.0,
                reserved_memory_mb=1024,
                reserved_disk_mb=1024,
            ),
        )
        pending = Workspace(
            name="pending",
            owner_user_id=int(user.user_auth_id),
            status=WorkspaceStatus.CREATING.value,
            execution_node_id=int(node.id),
        )
        session.add(pending)
        session.commit()
        session.refresh(pending)
        session.add(
            WorkspaceJob(
                workspace_id=int(pending.workspace_id),
                job_type=WorkspaceJobType.CREATE.value,
                status=WorkspaceJobStatus.QUEUED.value,
                requested_by_user_id=int(user.user_auth_id),
                requested_config_version=1,
            ),
        )
        session.commit()

        before = [(n.node_key, n.status, n.schedulable) for n in session.exec(select(ExecutionNode)).all()]
        with patch("app.services.autoscaler_service.service.get_settings") as mock_settings:
            mock_settings.return_value = SimpleNamespace(
                devnest_autoscaler_enabled=True,
                devnest_autoscaler_evaluate_only=True,
                devnest_autoscaler_min_nodes=1,
                devnest_autoscaler_max_nodes=5,
                devnest_autoscaler_min_idle_slots=1,
                devnest_autoscaler_max_concurrent_provisioning=3,
                devnest_autoscaler_scale_out_cooldown_seconds=0,
                devnest_autoscaler_scale_in_cooldown_seconds=0,
                devnest_enable_multi_node_scheduling=True,
                devnest_node_provider="all",
                devnest_require_fresh_node_heartbeat=False,
                aws_region="us-east-1",
                devnest_ec2_ami_id="ami-12345678",
                devnest_ec2_instance_type="t3.micro",
                devnest_ec2_subnet_id="subnet-12345678",
                devnest_ec2_security_group_ids="sg-12345678",
                devnest_ec2_instance_profile="DevNestExecutionNodeProfile",
                devnest_ec2_key_name="",
                devnest_ec2_default_execution_mode="ssm_docker",
                devnest_ec2_bootstrap_prebaked=True,
                devnest_ec2_user_data="",
                devnest_ec2_user_data_b64="",
            )
            decision = evaluate_fleet_autoscaler_tick(session)
        after = [(n.node_key, n.status, n.schedulable) for n in session.exec(select(ExecutionNode)).all()]

    assert decision.scale_out_recommended is True
    assert decision.action == "suppressed_by_config"
    assert decision.suppressed_by_config is True
    assert decision.capacity.pending_placement_jobs == 1
    assert decision.capacity.free_slots == 0
    assert before == after


def test_evaluate_only_tick_reports_no_action_for_idle_capacity(autoscaler_unit_engine) -> None:
    with Session(autoscaler_unit_engine) as session:
        session.add(
            ExecutionNode(
                node_key="ec2-idle",
                name="ec2-idle",
                provider_type=ExecutionNodeProviderType.EC2.value,
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                total_cpu=2.0,
                total_memory_mb=4096,
                allocatable_cpu=2.0,
                allocatable_memory_mb=4096,
                max_workspaces=4,
            ),
        )
        session.commit()
        with patch("app.services.autoscaler_service.service.get_settings") as mock_settings:
            mock_settings.return_value = SimpleNamespace(
                devnest_autoscaler_enabled=True,
                devnest_autoscaler_evaluate_only=True,
                devnest_autoscaler_min_nodes=1,
                devnest_autoscaler_max_nodes=5,
                devnest_autoscaler_min_idle_slots=1,
                devnest_autoscaler_max_concurrent_provisioning=3,
                devnest_autoscaler_scale_out_cooldown_seconds=0,
                devnest_autoscaler_scale_in_cooldown_seconds=0,
                devnest_enable_multi_node_scheduling=True,
                devnest_node_provider="all",
                devnest_require_fresh_node_heartbeat=False,
                aws_region="us-east-1",
                devnest_ec2_ami_id="ami-12345678",
                devnest_ec2_instance_type="t3.micro",
                devnest_ec2_subnet_id="subnet-12345678",
                devnest_ec2_security_group_ids="sg-12345678",
                devnest_ec2_instance_profile="DevNestExecutionNodeProfile",
                devnest_ec2_key_name="",
                devnest_ec2_default_execution_mode="ssm_docker",
                devnest_ec2_bootstrap_prebaked=True,
                devnest_ec2_user_data="",
                devnest_ec2_user_data_b64="",
            )
            decision = evaluate_fleet_autoscaler_tick(session)

    assert decision.action == "no_action"
    assert decision.no_action is True
    assert decision.capacity.ready_schedulable_ec2_nodes == 1
    assert decision.capacity.free_slots == 4


@pytest.mark.parametrize(
    ("reserved_cpu", "reserved_memory_mb", "reserved_disk_mb", "reason_fragment"),
    [
        (2.0, 0, 0, "free_cpu 0.0 < required_cpu 1.0"),
        (0.0, 1024, 0, "free_memory_mb 0 < required_memory_mb 512"),
        (0.0, 0, 4096, "free_disk_mb 0 < required_disk_mb 4096"),
    ],
)
def test_evaluate_tick_recommends_scale_out_when_required_resource_is_exhausted(
    autoscaler_unit_engine,
    reserved_cpu: float,
    reserved_memory_mb: int,
    reserved_disk_mb: int,
    reason_fragment: str,
) -> None:
    with Session(autoscaler_unit_engine) as session:
        user = UserAuth(
            username=f"auto-{reason_fragment[:4]}",
            email=f"{reason_fragment[:4]}@example.com",
            password_hash="x",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        node = ExecutionNode(
            node_key="ec2-resource-bound",
            name="ec2-resource-bound",
            provider_type=ExecutionNodeProviderType.EC2.value,
            status=ExecutionNodeStatus.READY.value,
            schedulable=True,
            total_cpu=2.0,
            total_memory_mb=1024,
            allocatable_cpu=2.0,
            allocatable_memory_mb=1024,
            allocatable_disk_mb=4096,
            max_workspaces=64,
        )
        session.add(node)
        session.commit()
        session.refresh(node)
        ws = Workspace(
            name="busy",
            owner_user_id=int(user.user_auth_id),
            status=WorkspaceStatus.RUNNING.value,
            execution_node_id=int(node.id),
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
        session.add(
            WorkspaceRuntime(
                workspace_id=int(ws.workspace_id),
                node_id="ec2-resource-bound",
                reserved_cpu=reserved_cpu,
                reserved_memory_mb=reserved_memory_mb,
                reserved_disk_mb=reserved_disk_mb,
            ),
        )
        session.commit()

        with patch("app.services.autoscaler_service.service.get_settings") as mock_settings:
            mock_settings.return_value = _scale_out_settings()
            decision = evaluate_fleet_autoscaler_tick(session)

    assert decision.scale_out_recommended is True
    assert decision.action == "scale_out_recommended"
    assert decision.capacity.free_slots == 63
    assert any(reason_fragment in reason for reason in decision.reasons)


def test_evaluate_tick_free_slots_high_but_cpu_zero_triggers_scale_out(autoscaler_unit_engine) -> None:
    with Session(autoscaler_unit_engine) as session:
        user = UserAuth(username="cpu-zero", email="cpu-zero@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)
        node = ExecutionNode(
            node_key="ec2-cpu-zero",
            name="ec2-cpu-zero",
            provider_type=ExecutionNodeProviderType.EC2.value,
            status=ExecutionNodeStatus.READY.value,
            schedulable=True,
            total_cpu=6.0,
            total_memory_mb=8192,
            allocatable_cpu=6.0,
            allocatable_memory_mb=8192,
            allocatable_disk_mb=102400,
            max_workspaces=64,
        )
        session.add(node)
        session.commit()
        session.refresh(node)
        for idx in range(6):
            ws = Workspace(
                name=f"busy-{idx}",
                owner_user_id=int(user.user_auth_id),
                status=WorkspaceStatus.RUNNING.value,
                execution_node_id=int(node.id),
            )
            session.add(ws)
            session.commit()
            session.refresh(ws)
            session.add(
                WorkspaceRuntime(
                    workspace_id=int(ws.workspace_id),
                    node_id="ec2-cpu-zero",
                    reserved_cpu=1.0,
                    reserved_memory_mb=512,
                    reserved_disk_mb=4096,
                ),
            )
        session.commit()

        with patch("app.services.autoscaler_service.service.get_settings") as mock_settings:
            mock_settings.return_value = _scale_out_settings()
            decision = evaluate_fleet_autoscaler_tick(session)

    assert decision.capacity.free_cpu == 0.0
    assert decision.capacity.active_slots == 6
    assert decision.capacity.free_slots == 58
    assert decision.scale_out_recommended is True
    assert decision.action == "scale_out_recommended"
    assert any("free_cpu 0.0 < required_cpu 1.0" in reason for reason in decision.reasons)


def test_recent_placement_failure_signal_triggers_scale_out_even_without_pending_jobs(
    autoscaler_unit_engine,
) -> None:
    with Session(autoscaler_unit_engine) as session:
        user = UserAuth(username="recent-demand", email="recent-demand@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)
        ws = Workspace(
            name="recent-placement-demand",
            owner_user_id=int(user.user_auth_id),
            status=WorkspaceStatus.PENDING.value,
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
        session.add(
            ExecutionNode(
                node_key="ec2-fragmented",
                name="ec2-fragmented",
                provider_type=ExecutionNodeProviderType.EC2.value,
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                total_cpu=4.0,
                total_memory_mb=8192,
                allocatable_cpu=4.0,
                allocatable_memory_mb=8192,
                allocatable_disk_mb=102400,
                max_workspaces=64,
            ),
        )
        session.commit()
        record_placement_failed_scale_out_signal(
            session,
            workspace_id=int(ws.workspace_id),
            workspace_job_id=None,
            job_type=WorkspaceJobType.CREATE.value,
            detail="No schedulable node qualified for placement",
            requested_cpu=1.0,
            requested_memory_mb=512,
            requested_disk_mb=4096,
            actor_user_id=None,
            correlation_id="test-placement-failed",
        )
        session.commit()

        with patch("app.services.autoscaler_service.service.get_settings") as mock_settings:
            mock_settings.return_value = _scale_out_settings()
            decision = evaluate_fleet_autoscaler_tick(session)

    assert decision.capacity.pending_workspace_jobs == 0
    assert decision.capacity.recent_placement_failures == 1
    assert decision.capacity.pending_placement_jobs == 1
    assert decision.scale_out_recommended is True
    assert decision.action == "scale_out_recommended"
    assert any("recent placement failure demand signals=1" in reason for reason in decision.reasons)


def test_recent_placement_failure_signal_is_ignored_after_demand_disappears(
    autoscaler_unit_engine,
) -> None:
    with Session(autoscaler_unit_engine) as session:
        session.add(
            ExecutionNode(
                node_key="ec2-empty",
                name="ec2-empty",
                provider_type=ExecutionNodeProviderType.EC2.value,
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                total_cpu=4.0,
                total_memory_mb=8192,
                allocatable_cpu=4.0,
                allocatable_memory_mb=8192,
                allocatable_disk_mb=102400,
                max_workspaces=64,
            ),
        )
        session.commit()
        record_placement_failed_scale_out_signal(
            session,
            job_type=WorkspaceJobType.CREATE.value,
            detail="placement.no_schedulable_node",
        )
        session.commit()

        with patch("app.services.autoscaler_service.service.get_settings") as mock_settings:
            mock_settings.return_value = _scale_out_settings()
            decision = evaluate_fleet_autoscaler_tick(session)

    assert decision.capacity.pending_workspace_jobs == 0
    assert decision.capacity.recent_placement_failures == 0
    assert decision.capacity.pending_placement_jobs == 0
    assert decision.scale_out_recommended is False
    assert decision.action == "no_action"


def test_empty_fleet_without_workspace_or_job_demand_does_not_scale_out(
    autoscaler_unit_engine,
) -> None:
    with Session(autoscaler_unit_engine) as session:
        record_placement_failed_scale_out_signal(
            session,
            job_type=WorkspaceJobType.CREATE.value,
            detail="placement.no_schedulable_node",
        )
        session.commit()

        with patch("app.services.autoscaler_service.service.get_settings") as mock_settings:
            mock_settings.return_value = _scale_out_settings()
            decision = evaluate_fleet_autoscaler_tick(session)

    assert decision.capacity.total_nodes == 0
    assert decision.capacity.pending_workspace_jobs == 0
    assert decision.capacity.recent_placement_failures == 0
    assert decision.capacity.pending_placement_jobs == 0
    assert decision.scale_out_recommended is False
    assert decision.action == "no_action"


def test_scale_out_tick_provisions_after_recent_placement_failure_signal(
    autoscaler_unit_engine,
) -> None:
    with Session(autoscaler_unit_engine) as session:
        user = UserAuth(username="recent-provision", email="recent-provision@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)
        ws = Workspace(
            name="recent-provision-demand",
            owner_user_id=int(user.user_auth_id),
            status=WorkspaceStatus.PENDING.value,
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
        session.add(
            ExecutionNode(
                node_key="ec2-current",
                name="ec2-current",
                provider_type=ExecutionNodeProviderType.EC2.value,
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                total_cpu=4.0,
                total_memory_mb=8192,
                allocatable_cpu=4.0,
                allocatable_memory_mb=8192,
                allocatable_disk_mb=102400,
                max_workspaces=64,
            ),
        )
        session.commit()
        record_placement_failed_scale_out_signal(
            session,
            workspace_id=int(ws.workspace_id),
            job_type=WorkspaceJobType.CREATE.value,
            detail="placement.no_schedulable_node",
        )
        session.commit()

        with (
            patch("app.services.autoscaler_service.service.get_settings") as mock_settings,
            patch("app.services.autoscaler_service.service.provision_ec2_node") as mock_provision,
        ):
            settings = _scale_out_settings()
            settings.devnest_ec2_bootstrap_prebaked = False
            settings.devnest_ec2_heartbeat_internal_api_base_url = "http://api.internal:8000"
            settings.internal_api_key_infrastructure = "infra-secret"
            settings.internal_api_key = ""
            settings.workspace_projects_base = "/var/lib/devnest/workspace-projects"
            settings.devnest_ec2_workspace_projects_base = "/var/lib/devnest/workspace-projects"
            settings.devnest_node_heartbeat_interval_seconds = 30
            settings.devnest_ec2_extra_tags = ""
            mock_settings.return_value = settings

            def _fake_provision(sess, request=None, wait_until_running=True):
                assert request is not None
                node = ExecutionNode(
                    node_key=request.node_key,
                    name=request.node_key,
                    provider_type=ExecutionNodeProviderType.EC2.value,
                    provider_instance_id="i-placementfailure",
                    status=ExecutionNodeStatus.PROVISIONING.value,
                    schedulable=False,
                    total_cpu=2.0,
                    total_memory_mb=4096,
                    allocatable_cpu=2.0,
                    allocatable_memory_mb=4096,
                )
                sess.add(node)
                sess.flush()
                return node

            mock_provision.side_effect = _fake_provision
            decision, node = run_scale_out_tick(session)

    assert decision.scale_out_recommended is True
    assert decision.action == "scale_out_recommended"
    assert node is not None
    assert node.provider_instance_id == "i-placementfailure"
    assert mock_provision.call_count == 1


def test_autoscaler_loop_tick_provisions_after_recent_placement_failure_signal(
    autoscaler_unit_engine,
) -> None:
    with Session(autoscaler_unit_engine) as session:
        user = UserAuth(username="recent-loop", email="recent-loop@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)
        ws = Workspace(
            name="recent-loop-demand",
            owner_user_id=int(user.user_auth_id),
            status=WorkspaceStatus.PENDING.value,
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
        session.add(
            ExecutionNode(
                node_key="ec2-current-loop",
                name="ec2-current-loop",
                provider_type=ExecutionNodeProviderType.EC2.value,
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                total_cpu=4.0,
                total_memory_mb=8192,
                allocatable_cpu=4.0,
                allocatable_memory_mb=8192,
                allocatable_disk_mb=102400,
                max_workspaces=64,
            ),
        )
        record_placement_failed_scale_out_signal(
            session,
            workspace_id=int(ws.workspace_id),
            job_type=WorkspaceJobType.CREATE.value,
            detail="placement.no_schedulable_node",
        )
        session.commit()

    with (
        patch("app.services.autoscaler_service.service.get_settings") as mock_settings,
        patch("app.services.autoscaler_service.service.provision_ec2_node") as mock_provision,
    ):
        settings = _scale_out_settings()
        settings.devnest_ec2_bootstrap_prebaked = False
        settings.devnest_ec2_heartbeat_internal_api_base_url = "http://api.internal:8000"
        settings.internal_api_key_infrastructure = "infra-secret"
        settings.internal_api_key = ""
        settings.workspace_projects_base = "/var/lib/devnest/workspace-projects"
        settings.devnest_ec2_workspace_projects_base = "/var/lib/devnest/workspace-projects"
        settings.devnest_node_heartbeat_interval_seconds = 30
        settings.devnest_ec2_extra_tags = ""
        mock_settings.return_value = settings

        def _fake_provision(sess, request=None, wait_until_running=True):
            assert request is not None
            node = ExecutionNode(
                node_key=request.node_key,
                name=request.node_key,
                provider_type=ExecutionNodeProviderType.EC2.value,
                provider_instance_id="i-loopplacementfailure",
                status=ExecutionNodeStatus.PROVISIONING.value,
                schedulable=False,
                total_cpu=2.0,
                total_memory_mb=4096,
                allocatable_cpu=2.0,
                allocatable_memory_mb=4096,
            )
            sess.add(node)
            sess.flush()
            return node

        mock_provision.side_effect = _fake_provision
        action, node_key = run_autoscaler_loop_tick(autoscaler_unit_engine)

    with Session(autoscaler_unit_engine) as session:
        rows = list(session.exec(select(ExecutionNode)).all())

    assert action == "scale_out_recommended"
    assert node_key is not None
    assert mock_provision.call_count == 1
    assert any(row.provider_instance_id == "i-loopplacementfailure" for row in rows)


def test_scale_down_idle_node_terminates(autoscaler_unit_engine) -> None:
    old = datetime.now(timezone.utc) - timedelta(seconds=900)
    with Session(autoscaler_unit_engine) as session:
        _seed_ec2_node(session, "ec2-idle-a", created_at=old)
        _seed_ec2_node(session, "ec2-idle-b", created_at=old)
        _seed_ec2_node(session, "ec2-idle-c", created_at=old)
        session.commit()

    with (
        patch("app.services.autoscaler_service.service.get_settings") as mock_settings,
        patch("app.services.autoscaler_service.service.terminate_ec2_node", side_effect=_fake_terminate) as term,
    ):
        mock_settings.return_value = _scale_down_settings()
        node_key = run_autoscaler_scale_down_tick(autoscaler_unit_engine)

    with Session(autoscaler_unit_engine) as session:
        node = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == node_key)).first()
        assert node is not None
        assert node.status == ExecutionNodeStatus.TERMINATED.value
        assert node.schedulable is False
    assert node_key == "ec2-idle-a"
    assert term.call_count == 1


def test_scale_down_min_ec2_zero_reclaims_last_idle_ec2_node(autoscaler_unit_engine) -> None:
    old = datetime.now(timezone.utc) - timedelta(seconds=900)
    with Session(autoscaler_unit_engine) as session:
        _seed_ec2_node(session, "ec2-last-idle", created_at=old)
        session.commit()

    with (
        patch("app.services.autoscaler_service.service.get_settings") as mock_settings,
        patch("app.services.autoscaler_service.service.terminate_ec2_node", side_effect=_fake_terminate) as term,
    ):
        mock_settings.return_value = _scale_down_settings(min_nodes=0, min_ec2_before_reclaim=0)
        node_key = run_autoscaler_scale_down_tick(autoscaler_unit_engine)

    with Session(autoscaler_unit_engine) as session:
        node = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == "ec2-last-idle")).first()
        assert node is not None
        assert node.status == ExecutionNodeStatus.TERMINATED.value
        assert node.schedulable is False
    assert node_key == "ec2-last-idle"
    assert term.call_count == 1


def test_idle_ec2_node_with_min_ec2_zero_recommends_scale_in_without_demand(
    autoscaler_unit_engine,
) -> None:
    old = datetime.now(timezone.utc) - timedelta(seconds=900)
    with Session(autoscaler_unit_engine) as session:
        _seed_ec2_node(session, "ec2-decision-idle", created_at=old)
        session.commit()

        with patch("app.services.autoscaler_service.service.get_settings") as mock_settings:
            mock_settings.return_value = _scale_down_settings(min_nodes=0, min_ec2_before_reclaim=0)
            decision = evaluate_fleet_autoscaler_tick(session)

    assert decision.capacity.pending_workspace_jobs == 0
    assert decision.capacity.pending_placement_jobs == 0
    assert decision.scale_in_recommended is True
    assert decision.scale_out_recommended is False
    assert decision.no_action is False
    assert decision.action == "scale_in_recommended"
    assert any("scale-in recommended" in reason for reason in decision.reasons)


def test_autoscaler_loop_tick_terminates_selected_idle_node(autoscaler_unit_engine) -> None:
    old = datetime.now(timezone.utc) - timedelta(seconds=900)
    with Session(autoscaler_unit_engine) as session:
        _seed_ec2_node(session, "ec2-loop-scale-in", created_at=old)
        session.commit()

    with (
        patch("app.services.autoscaler_service.service.get_settings") as mock_settings,
        patch("app.services.autoscaler_service.service.terminate_ec2_node", side_effect=_fake_terminate) as term,
    ):
        mock_settings.return_value = _scale_down_settings(min_nodes=0, min_ec2_before_reclaim=0)
        action, node_key = run_autoscaler_loop_tick(autoscaler_unit_engine)

    with Session(autoscaler_unit_engine) as session:
        node = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == "ec2-loop-scale-in")).first()
        assert node is not None
        assert node.status == ExecutionNodeStatus.TERMINATED.value
        assert node.schedulable is False
    assert action == "scale_in_recommended"
    assert node_key == "ec2-loop-scale-in"
    assert term.call_count == 1


def test_scale_down_active_node_does_not_terminate(autoscaler_unit_engine) -> None:
    old = datetime.now(timezone.utc) - timedelta(seconds=900)
    with Session(autoscaler_unit_engine) as session:
        user = UserAuth(username="busy-auto", email="busy-auto@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)
        active = _seed_ec2_node(session, "ec2-active", created_at=old)
        fresh = datetime.now(timezone.utc)
        _seed_ec2_node(session, "ec2-fresh-a", created_at=fresh)
        _seed_ec2_node(session, "ec2-fresh-b", created_at=fresh)
        session.commit()
        ws = Workspace(
            name="pending-on-node",
            owner_user_id=int(user.user_auth_id),
            status=WorkspaceStatus.PENDING.value,
            execution_node_id=int(active.id),
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
        session.add(WorkspaceRuntime(workspace_id=int(ws.workspace_id), node_id="ec2-active"))
        session.commit()

    with (
        patch("app.services.autoscaler_service.service.get_settings") as mock_settings,
        patch("app.services.autoscaler_service.service.terminate_ec2_node", side_effect=_fake_terminate) as term,
    ):
        mock_settings.return_value = _scale_down_settings()
        with Session(autoscaler_unit_engine) as session:
            node = reclaim_one_idle_ec2_node(session)

    assert node is None
    assert term.call_count == 0


def test_scale_down_idle_ignores_updated_at_and_last_resource_check(autoscaler_unit_engine) -> None:
    """Resource-monitor timestamps must not defer scale-down; idle age uses ``created_at`` only."""
    old_created = datetime.now(timezone.utc) - timedelta(seconds=900)
    fresh = datetime.now(timezone.utc)
    with Session(autoscaler_unit_engine) as session:
        _seed_ec2_node(
            session,
            "ec2-resource-noise",
            created_at=old_created,
            updated_at=fresh,
            last_resource_check_at=fresh,
        )
        session.commit()

    with (
        patch("app.services.autoscaler_service.service.get_settings") as mock_settings,
        patch("app.services.autoscaler_service.service.terminate_ec2_node", side_effect=_fake_terminate) as term,
    ):
        mock_settings.return_value = _scale_down_settings(min_nodes=0, min_ec2_before_reclaim=0)
        node_key = run_autoscaler_scale_down_tick(autoscaler_unit_engine)

    assert node_key == "ec2-resource-noise"
    assert term.call_count == 1


def test_scale_down_suppressed_when_created_at_too_recent_despite_zero_workloads(autoscaler_unit_engine) -> None:
    """No workloads but ``created_at`` inside ``scale_down_idle_seconds`` → no reclaim."""
    recent_created = datetime.now(timezone.utc) - timedelta(seconds=60)
    with Session(autoscaler_unit_engine) as session:
        _seed_ec2_node(session, "ec2-young", created_at=recent_created)
        session.commit()

    with (
        patch("app.services.autoscaler_service.service.get_settings") as mock_settings,
        patch("app.services.autoscaler_service.service.terminate_ec2_node", side_effect=_fake_terminate) as term,
    ):
        mock_settings.return_value = _scale_down_settings(min_nodes=0, min_ec2_before_reclaim=0, idle_seconds=300)
        node_key = run_autoscaler_scale_down_tick(autoscaler_unit_engine)

    assert node_key is None
    assert term.call_count == 0


def test_evaluate_scale_down_emits_idle_and_runtime_logs(autoscaler_unit_engine) -> None:
    old = datetime.now(timezone.utc) - timedelta(seconds=900)
    with Session(autoscaler_unit_engine) as session:
        _seed_ec2_node(session, "ec2-log-a", created_at=old)
        _seed_ec2_node(session, "ec2-log-b", created_at=old)
        session.commit()

    from app.libs.observability.log_events import LogEvent

    with patch("app.services.autoscaler_service.service.get_settings") as mock_settings:
        mock_settings.return_value = SimpleNamespace(devnest_autoscaler_min_ec2_nodes_before_reclaim=2)
        with Session(autoscaler_unit_engine) as session:
            with patch("app.services.autoscaler_service.service.log_event") as mock_log:
                evaluate_scale_down(session)
        codes = [c.args[1] for c in mock_log.call_args_list]
        assert LogEvent.AUTOSCALER_SCALE_DOWN_RUNTIME_COUNT in codes
        assert LogEvent.AUTOSCALER_SCALE_DOWN_IDLE_DURATION in codes


def test_scale_down_min_node_floor_respected(autoscaler_unit_engine) -> None:
    old = datetime.now(timezone.utc) - timedelta(seconds=900)
    with Session(autoscaler_unit_engine) as session:
        _seed_ec2_node(session, "ec2-floor-a", created_at=old)
        _seed_ec2_node(session, "ec2-floor-b", created_at=old)
        session.commit()

    with (
        patch("app.services.autoscaler_service.service.get_settings") as mock_settings,
        patch("app.services.autoscaler_service.service.terminate_ec2_node", side_effect=_fake_terminate) as term,
    ):
        mock_settings.return_value = _scale_down_settings(min_nodes=2, min_ec2_before_reclaim=2)
        node_key = run_autoscaler_scale_down_tick(autoscaler_unit_engine)

    assert node_key is None
    assert term.call_count == 0


def test_scale_down_cooldown_respected(autoscaler_unit_engine) -> None:
    old = datetime.now(timezone.utc) - timedelta(seconds=900)
    with Session(autoscaler_unit_engine) as session:
        _seed_ec2_node(session, "ec2-cool-a", created_at=old)
        _seed_ec2_node(session, "ec2-cool-b", created_at=old)
        _seed_ec2_node(session, "ec2-cool-c", created_at=old)
        _seed_ec2_node(
            session,
            "ec2-just-terminated",
            created_at=old,
            updated_at=datetime.now(timezone.utc),
            status=ExecutionNodeStatus.TERMINATED.value,
            schedulable=False,
        )
        session.commit()

    with (
        patch("app.services.autoscaler_service.service.get_settings") as mock_settings,
        patch("app.services.autoscaler_service.service.terminate_ec2_node", side_effect=_fake_terminate) as term,
    ):
        mock_settings.return_value = _scale_down_settings(cooldown_seconds=900)
        node_key = run_autoscaler_scale_down_tick(autoscaler_unit_engine)

    assert node_key is None
    assert term.call_count == 0


def test_phase2_scale_out_tick_provisions_one_provisioning_unschedulable_node(autoscaler_unit_engine) -> None:
    with Session(autoscaler_unit_engine) as session:
        user = UserAuth(username="phase2-auto", email="phase2-auto@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)
        ws = Workspace(
            name="phase2-pending",
            owner_user_id=int(user.user_auth_id),
            status=WorkspaceStatus.PENDING.value,
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
        session.add(
            WorkspaceJob(
                workspace_id=int(ws.workspace_id),
                job_type=WorkspaceJobType.CREATE.value,
                status=WorkspaceJobStatus.QUEUED.value,
                requested_by_user_id=int(user.user_auth_id),
                requested_config_version=1,
            )
        )
        session.commit()
        with (
            patch("app.services.autoscaler_service.service.get_settings") as mock_settings,
            patch("app.services.autoscaler_service.service.provision_ec2_node") as mock_provision,
        ):
            mock_settings.return_value = SimpleNamespace(
                devnest_autoscaler_enabled=True,
                devnest_autoscaler_evaluate_only=False,
                devnest_autoscaler_min_nodes=1,
                devnest_autoscaler_max_nodes=5,
                devnest_autoscaler_min_idle_slots=1,
                devnest_autoscaler_max_concurrent_provisioning=3,
                devnest_autoscaler_scale_out_cooldown_seconds=0,
                devnest_autoscaler_scale_in_cooldown_seconds=0,
                devnest_enable_multi_node_scheduling=True,
                devnest_node_provider="all",
                devnest_require_fresh_node_heartbeat=False,
                aws_region="us-east-1",
                devnest_ec2_ami_id="ami-12345678",
                devnest_ec2_instance_type="t3.micro",
                devnest_ec2_subnet_id="subnet-12345678",
                devnest_ec2_security_group_ids="sg-12345678",
                devnest_ec2_instance_profile="DevNestExecutionNodeProfile",
                devnest_ec2_key_name="",
                devnest_ec2_default_execution_mode="ssm_docker",
                devnest_ec2_bootstrap_prebaked=False,
                devnest_ec2_heartbeat_internal_api_base_url="http://api.internal:8000",
                internal_api_key_infrastructure="infra-secret",
                internal_api_key="",
                workspace_projects_base="/var/lib/devnest/workspace-projects",
                devnest_ec2_workspace_projects_base="/var/lib/devnest/workspace-projects",
                devnest_node_heartbeat_interval_seconds=30,
                devnest_ec2_user_data="",
                devnest_ec2_user_data_b64="",
                devnest_ec2_extra_tags="",
            )

            def _fake_provision(sess, request=None, wait_until_running=True):
                assert request is not None
                assert request.node_key.startswith("ec2-autoscale-")
                assert "dnf install -y docker" in (request.user_data or "")
                assert "dnf install -y docker curl" not in (request.user_data or "")
                assert "devnest-node-heartbeat.service" in (request.user_data or "")
                assert request.node_key in (request.user_data or "")
                assert "/var/lib/devnest/workspace-projects" in (request.user_data or "")
                assert "/var/log/devnest/bootstrap.log" in (request.user_data or "")
                assert "StandardOutput=journal" in (request.user_data or "")
                node = ExecutionNode(
                    node_key=request.node_key,
                    name=request.node_key,
                    provider_type=ExecutionNodeProviderType.EC2.value,
                    provider_instance_id="i-0123456789abcdef0",
                    status=ExecutionNodeStatus.PROVISIONING.value,
                    schedulable=False,
                    total_cpu=2.0,
                    total_memory_mb=4096,
                    allocatable_cpu=2.0,
                    allocatable_memory_mb=4096,
                )
                sess.add(node)
                sess.flush()
                return node

            mock_provision.side_effect = _fake_provision
            decision, node = run_scale_out_tick(session)
            session.commit()

        rows = list(session.exec(select(ExecutionNode)).all())

    assert decision.action == "scale_out_recommended"
    assert node is not None
    assert mock_provision.call_count == 1
    assert len(rows) == 1
    assert rows[0].status == ExecutionNodeStatus.PROVISIONING.value
    assert rows[0].schedulable is False


def test_phase2_scale_out_tick_respects_max_nodes_cap(autoscaler_unit_engine) -> None:
    with Session(autoscaler_unit_engine) as session:
        user = UserAuth(username="cap-auto", email="cap-auto@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)
        ws = Workspace(
            name="cap-pending",
            owner_user_id=int(user.user_auth_id),
            status=WorkspaceStatus.PENDING.value,
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
        session.add(
            WorkspaceJob(
                workspace_id=int(ws.workspace_id),
                job_type=WorkspaceJobType.CREATE.value,
                status=WorkspaceJobStatus.QUEUED.value,
                requested_by_user_id=int(user.user_auth_id),
                requested_config_version=1,
            )
        )
        session.add(
            ExecutionNode(
                node_key="ec2-existing",
                name="ec2-existing",
                provider_type=ExecutionNodeProviderType.EC2.value,
                provider_instance_id="i-0123456789abcdef0",
                status=ExecutionNodeStatus.NOT_READY.value,
                schedulable=False,
                total_cpu=2.0,
                total_memory_mb=4096,
                allocatable_cpu=2.0,
                allocatable_memory_mb=4096,
            ),
        )
        session.commit()
        with (
            patch("app.services.autoscaler_service.service.get_settings") as mock_settings,
            patch("app.services.autoscaler_service.service.provision_ec2_node") as mock_provision,
        ):
            mock_settings.return_value = SimpleNamespace(
                devnest_autoscaler_enabled=True,
                devnest_autoscaler_evaluate_only=False,
                devnest_autoscaler_min_nodes=1,
                devnest_autoscaler_max_nodes=1,
                devnest_autoscaler_min_idle_slots=1,
                devnest_autoscaler_max_concurrent_provisioning=3,
                devnest_autoscaler_scale_out_cooldown_seconds=0,
                devnest_autoscaler_scale_in_cooldown_seconds=0,
                devnest_enable_multi_node_scheduling=True,
                devnest_node_provider="all",
                devnest_require_fresh_node_heartbeat=False,
                aws_region="us-east-1",
                devnest_ec2_ami_id="ami-12345678",
                devnest_ec2_instance_type="t3.micro",
                devnest_ec2_subnet_id="subnet-12345678",
                devnest_ec2_security_group_ids="sg-12345678",
                devnest_ec2_instance_profile="DevNestExecutionNodeProfile",
                devnest_ec2_key_name="",
                devnest_ec2_default_execution_mode="ssm_docker",
                devnest_ec2_bootstrap_prebaked=True,
                devnest_ec2_user_data="",
                devnest_ec2_user_data_b64="",
            )
            decision, node = run_scale_out_tick(session)

    assert decision.action == "suppressed_by_cap"
    assert decision.suppressed_by_cap is True
    assert node is None
    mock_provision.assert_not_called()


def test_ec2_autoscaler_config_errors_are_aggregated() -> None:
    settings = SimpleNamespace(
        aws_region="",
        devnest_ec2_ami_id="",
        devnest_ec2_instance_type="",
        devnest_ec2_subnet_id="",
        devnest_ec2_security_group_ids="",
        devnest_ec2_instance_profile="",
        devnest_ec2_key_name="",
        devnest_ec2_default_execution_mode="ssm_docker",
        devnest_ec2_bootstrap_prebaked=False,
        devnest_ec2_user_data="",
        devnest_ec2_user_data_b64="",
        devnest_ec2_extra_tags="",
    )

    errors = ec2_autoscaler_provisioning_config_errors(settings)

    assert any("AWS_REGION" in e for e in errors)
    assert any("DEVNEST_EC2_AMI_ID" in e for e in errors)
    assert any("DEVNEST_EC2_SUBNET_ID" in e for e in errors)
    assert any("DEVNEST_EC2_SECURITY_GROUP_IDS" in e for e in errors)
    assert any("DEVNEST_EC2_INSTANCE_PROFILE" in e for e in errors)
    assert any("bootstrap config" in e for e in errors)


def test_scale_in_recommended_execute_scale_down_calls_terminate_ec2(autoscaler_unit_engine) -> None:
    old = datetime.now(timezone.utc) - timedelta(seconds=900)
    with Session(autoscaler_unit_engine) as session:
        _seed_ec2_node(session, "ec2-exec-term-a", created_at=old)
        _seed_ec2_node(session, "ec2-exec-term-b", created_at=old)
        session.commit()

    with (
        patch("app.services.autoscaler_service.service.get_settings") as mock_settings,
        patch("app.services.autoscaler_service.service.terminate_ec2_node", side_effect=_fake_terminate) as term,
    ):
        mock_settings.return_value = _scale_down_settings()
        with Session(autoscaler_unit_engine) as session:
            decision = evaluate_fleet_autoscaler_tick(session)
            assert decision.action == "scale_in_recommended"
            out = execute_scale_down(session, decision)

    assert out is not None
    assert term.call_count == 1
    assert term.call_args.kwargs.get("node_key") == "ec2-exec-term-a"


def _terminate_asserts_draining_then_fake(session: Session, *, node_key: str, **_kw: object) -> ExecutionNode:
    row = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == node_key)).first()
    assert row is not None
    assert row.status == ExecutionNodeStatus.DRAINING.value
    assert row.schedulable is False
    return _fake_terminate(session, node_key=node_key)


def test_execute_scale_down_node_is_draining_before_terminate(autoscaler_unit_engine) -> None:
    old = datetime.now(timezone.utc) - timedelta(seconds=900)
    with Session(autoscaler_unit_engine) as session:
        _seed_ec2_node(session, "ec2-drain-seq-a", created_at=old)
        _seed_ec2_node(session, "ec2-drain-seq-b", created_at=old)
        session.commit()

    with (
        patch("app.services.autoscaler_service.service.get_settings") as mock_settings,
        patch(
            "app.services.autoscaler_service.service.terminate_ec2_node",
            side_effect=_terminate_asserts_draining_then_fake,
        ) as term,
    ):
        mock_settings.return_value = _scale_down_settings()
        with Session(autoscaler_unit_engine) as session:
            decision = evaluate_fleet_autoscaler_tick(session)
            execute_scale_down(session, decision)

    assert term.call_count == 1


def test_execute_scale_down_active_workspace_race_reverts_ready(autoscaler_unit_engine) -> None:
    old = datetime.now(timezone.utc) - timedelta(seconds=900)
    with Session(autoscaler_unit_engine) as session:
        _seed_ec2_node(session, "ec2-race-a", created_at=old)
        _seed_ec2_node(session, "ec2-race-b", created_at=old)
        session.commit()

    with (
        patch("app.services.autoscaler_service.service.get_settings") as mock_settings,
        patch("app.services.autoscaler_service.service.terminate_ec2_node") as term,
        patch(
            "app.services.autoscaler_service.service._active_workspace_count_for_scale_down",
            side_effect=[0, 1],
        ),
    ):
        mock_settings.return_value = _scale_down_settings()
        with Session(autoscaler_unit_engine) as session:
            decision = evaluate_fleet_autoscaler_tick(session)
            out = execute_scale_down(session, decision)

    assert out is None
    term.assert_not_called()
    with Session(autoscaler_unit_engine) as session:
        row = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == "ec2-race-a")).first()
        assert row is not None
        assert row.status == ExecutionNodeStatus.READY.value
        assert row.schedulable is True


def test_evaluate_recommends_scale_out_when_all_ec2_fail_host_resource_gate(
    autoscaler_unit_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fleet snapshot uses placement predicates (real settings): low disk excludes EC2 from schedulable pool."""
    monkeypatch.setenv("DEVNEST_NODE_RESOURCE_MONITOR_ENABLED", "true")
    monkeypatch.setenv("DEVNEST_NODE_PROVIDER", "ec2")
    monkeypatch.setenv("DEVNEST_ENABLE_MULTI_NODE_SCHEDULING", "true")
    get_settings.cache_clear()
    now = datetime.now(timezone.utc)
    with Session(autoscaler_unit_engine) as session:
        session.add(
            ExecutionNode(
                node_key="ec2-host-resource-blocked",
                name="ec2-host-resource-blocked",
                provider_type=ExecutionNodeProviderType.EC2.value,
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                total_cpu=4.0,
                total_memory_mb=8192,
                allocatable_cpu=4.0,
                allocatable_memory_mb=8192,
                allocatable_disk_mb=102_400,
                max_workspaces=8,
                disk_free_mb=4096,
                memory_free_mb=8192,
                last_resource_check_at=now,
            ),
        )
        session.commit()
        node = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == "ec2-host-resource-blocked")).first()
        assert node is not None
        user = UserAuth(username="hr-u1", password_hash="x", email="hr-u1@example.com")
        session.add(user)
        session.commit()
        session.refresh(user)
        session.add(
            Workspace(
                name="hr-ws",
                owner_user_id=int(user.user_auth_id),
                status=WorkspaceStatus.RUNNING.value,
                execution_node_id=int(node.id),
            ),
        )
        session.commit()

    with patch("app.services.autoscaler_service.service.get_settings") as mock_settings:
        mock_settings.return_value = SimpleNamespace(
            devnest_autoscaler_enabled=True,
            devnest_autoscaler_evaluate_only=True,
            devnest_autoscaler_min_nodes=1,
            devnest_autoscaler_max_nodes=5,
            devnest_autoscaler_min_idle_slots=1,
            devnest_autoscaler_max_concurrent_provisioning=3,
            devnest_autoscaler_scale_out_cooldown_seconds=0,
            devnest_autoscaler_scale_in_cooldown_seconds=0,
            devnest_enable_multi_node_scheduling=True,
            devnest_node_provider="ec2",
            devnest_require_fresh_node_heartbeat=False,
            aws_region="us-east-1",
            devnest_ec2_ami_id="ami-12345678",
            devnest_ec2_instance_type="t3.micro",
            devnest_ec2_subnet_id="subnet-12345678",
            devnest_ec2_security_group_ids="sg-12345678",
            devnest_ec2_instance_profile="DevNestExecutionNodeProfile",
            devnest_ec2_key_name="",
            devnest_ec2_default_execution_mode="ssm_docker",
            devnest_ec2_bootstrap_prebaked=True,
            devnest_ec2_user_data="",
            devnest_ec2_user_data_b64="",
        )
        with Session(autoscaler_unit_engine) as session:
            decision = evaluate_fleet_autoscaler_tick(session)

    assert decision.scale_out_recommended is True
    assert decision.capacity.ready_schedulable_nodes == 0
    assert decision.capacity.ready_schedulable_ec2_nodes == 0
    joined = " ".join(decision.reasons).lower()
    assert "capacity insufficient" in joined or "no ready schedulable nodes" in joined
