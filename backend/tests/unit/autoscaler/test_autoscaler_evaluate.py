"""Unit tests: autoscaler decisions (mocked settings / counts / EC2 request)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.autoscaler_service.service import (
    evaluate_scale_down,
    evaluate_scale_up,
    maybe_provision_on_no_schedulable_capacity,
)


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
