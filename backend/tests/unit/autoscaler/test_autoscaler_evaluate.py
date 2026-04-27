"""Unit tests: autoscaler decisions (mocked settings / counts / EC2 request)."""

from __future__ import annotations

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
    maybe_provision_on_no_schedulable_capacity,
    run_scale_out_tick,
)
from app.services.auth_service.models import UserAuth
from app.services.placement_service.models import ExecutionNode, ExecutionNodeProviderType, ExecutionNodeStatus
from app.services.workspace_service.models import Workspace, WorkspaceJob, WorkspaceRuntime
from app.services.workspace_service.models.enums import WorkspaceJobStatus, WorkspaceJobType, WorkspaceStatus


@pytest.fixture
def autoscaler_unit_engine() -> Engine:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


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
@patch("app.services.autoscaler_service.service.count_ec2_ready_schedulable", return_value=2)
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


@patch("app.services.autoscaler_service.service.count_ec2_ready_schedulable", return_value=1)
@patch("app.services.autoscaler_service.service.get_settings")
def test_evaluate_scale_down_refuses_below_min_ready(
    mock_settings: MagicMock,
    _n_ready: MagicMock,
) -> None:
    mock_settings.return_value = SimpleNamespace(devnest_autoscaler_min_ec2_nodes_before_reclaim=2)
    ev = evaluate_scale_down(MagicMock())
    assert ev.node_key is None
    assert "below minimum" in ev.reason
    assert "last-node safety" in ev.reason


@patch("app.services.autoscaler_service.service.count_ec2_ready_schedulable", return_value=2)
@patch("app.services.autoscaler_service.service.get_settings")
def test_evaluate_scale_down_effective_min_ready_is_at_least_two(
    mock_settings: MagicMock,
    _n_ready: MagicMock,
) -> None:
    """Misconfigured ``min_ec2_nodes_before_reclaim=1`` must not weaken last-node safety."""
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
    assert "minimum before reclaim=2" in ev.reason


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


def test_phase2_scale_out_tick_provisions_one_provisioning_unschedulable_node(autoscaler_unit_engine) -> None:
    with Session(autoscaler_unit_engine) as session:
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
                assert "dnf install -y docker curl" in (request.user_data or "")
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
