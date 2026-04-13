"""Unit tests: cost-aware autoscaler decisions.

Covers:
- Scale-up suppression when idle EC2 nodes exist (prefer reuse over provisioning)
- Scale-up proceeds when no idle EC2 nodes exist
- idle_ec2_node_count field in ScaleUpEvaluation
- Scale-down picks smallest-capacity idle node (cost-aware: preserve large nodes)
- Scale-down suppressed when all nodes have workloads
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.autoscaler_service.service import (
    _count_idle_ec2_nodes,
    evaluate_scale_down,
    evaluate_scale_up,
)
from app.services.placement_service.models import ExecutionNode, ExecutionNodeProviderType, ExecutionNodeStatus


def _ec2_node(key: str, cpu: float = 2.0, mem: int = 4096) -> ExecutionNode:
    return ExecutionNode(
        node_key=key,
        name=key,
        provider_type=ExecutionNodeProviderType.EC2.value,
        status=ExecutionNodeStatus.READY.value,
        schedulable=True,
        total_cpu=cpu,
        total_memory_mb=mem,
        allocatable_cpu=cpu,
        allocatable_memory_mb=mem,
    )


# ---------------------------------------------------------------------------
# Scale-up suppression
# ---------------------------------------------------------------------------

class TestScaleUpIdleNodeSuppression:
    @patch("app.services.autoscaler_service.service.count_ec2_provisioning_nodes", return_value=0)
    @patch("app.services.autoscaler_service.service._count_idle_ec2_nodes", return_value=2)
    @patch("app.services.autoscaler_service.service.get_settings")
    def test_suppressed_when_idle_nodes_exist(
        self, mock_settings: MagicMock, _idle: MagicMock, _prov: MagicMock
    ) -> None:
        mock_settings.return_value = SimpleNamespace(
            devnest_autoscaler_enabled=True,
            devnest_node_provider="ec2",
            devnest_autoscaler_max_concurrent_provisioning=3,
        )
        ev = evaluate_scale_up(MagicMock(), insufficient_capacity=True)
        assert ev.should_provision is False
        assert ev.idle_ec2_node_count == 2
        assert "suppressed" in ev.reason
        assert "idle" in ev.reason

    @patch("app.services.autoscaler_service.service.count_ec2_provisioning_nodes", return_value=0)
    @patch("app.services.autoscaler_service.service._count_idle_ec2_nodes", return_value=0)
    @patch("app.services.autoscaler_service.service.get_settings")
    @patch("app.services.autoscaler_service.service.Ec2ProvisionRequest")
    def test_proceeds_when_no_idle_nodes(
        self, mock_req: MagicMock, mock_settings: MagicMock, _idle: MagicMock, _prov: MagicMock
    ) -> None:
        mock_settings.return_value = SimpleNamespace(
            devnest_autoscaler_enabled=True,
            devnest_node_provider="ec2",
            devnest_autoscaler_max_concurrent_provisioning=3,
        )
        inst = MagicMock()
        mock_req.from_settings.return_value = inst
        ev = evaluate_scale_up(MagicMock(), insufficient_capacity=True)
        assert ev.should_provision is True
        assert ev.idle_ec2_node_count == 0

    @patch("app.services.autoscaler_service.service.count_ec2_provisioning_nodes", return_value=0)
    @patch("app.services.autoscaler_service.service.get_settings")
    def test_autoscaler_disabled_returns_zero_idle_count(
        self, mock_settings: MagicMock, _prov: MagicMock
    ) -> None:
        mock_settings.return_value = SimpleNamespace(devnest_autoscaler_enabled=False)
        ev = evaluate_scale_up(MagicMock(), insufficient_capacity=True)
        assert ev.should_provision is False
        assert ev.idle_ec2_node_count == 0

    @patch("app.services.autoscaler_service.service.count_ec2_provisioning_nodes", return_value=0)
    @patch("app.services.autoscaler_service.service._count_idle_ec2_nodes", return_value=1)
    @patch("app.services.autoscaler_service.service.get_settings")
    def test_suppression_logged_at_info(
        self, mock_settings: MagicMock, _idle: MagicMock, _prov: MagicMock
    ) -> None:
        """The suppression event should be recorded (LogEvent.AUTOSCALER_SCALE_UP_SUPPRESSED)."""
        mock_settings.return_value = SimpleNamespace(
            devnest_autoscaler_enabled=True,
            devnest_node_provider="ec2",
            devnest_autoscaler_max_concurrent_provisioning=3,
        )
        with patch("app.services.autoscaler_service.service.log_event") as mock_log:
            ev = evaluate_scale_up(MagicMock(), insufficient_capacity=True)
        assert ev.should_provision is False
        mock_log.assert_called_once()
        call_args = mock_log.call_args
        from app.libs.observability.log_events import LogEvent
        assert call_args.args[1] == LogEvent.AUTOSCALER_SCALE_UP_SUPPRESSED


# ---------------------------------------------------------------------------
# Cost-aware scale-down candidate selection
# ---------------------------------------------------------------------------

class TestScaleDownCostAwareCandidateSelection:
    @patch(
        "app.services.autoscaler_service.service._workload_counts_by_node_keys",
        return_value={"big": 0, "small": 0},
    )
    @patch("app.services.autoscaler_service.service.count_ec2_ready_schedulable", return_value=3)
    @patch("app.services.autoscaler_service.service.get_settings")
    def test_prefers_smallest_capacity_idle_node(
        self, mock_settings: MagicMock, _n_ready: MagicMock, _counts: MagicMock
    ) -> None:
        """Should pick the smaller node, not the alphabetically first one."""
        mock_settings.return_value = SimpleNamespace(devnest_autoscaler_min_ec2_nodes_before_reclaim=2)

        n_big = _ec2_node("big", cpu=8.0, mem=16384)
        n_small = _ec2_node("small", cpu=2.0, mem=4096)

        session = MagicMock()

        def _exec(_stmt: object) -> MagicMock:
            m = MagicMock()
            m.all = lambda: [n_big, n_small]
            return m

        session.exec.side_effect = _exec
        ev = evaluate_scale_down(session)
        assert ev.node_key == "small", "smallest-capacity idle node should be reclaimed first"
        assert "allocatable_cpu=2.0" in ev.reason

    @patch(
        "app.services.autoscaler_service.service._workload_counts_by_node_keys",
        return_value={"ec2-a": 0, "ec2-b": 0, "ec2-c": 0},
    )
    @patch("app.services.autoscaler_service.service.count_ec2_ready_schedulable", return_value=4)
    @patch("app.services.autoscaler_service.service.get_settings")
    def test_tiebreak_by_node_key_when_equal_capacity(
        self, mock_settings: MagicMock, _n_ready: MagicMock, _counts: MagicMock
    ) -> None:
        """When capacity is equal, tiebreak by node_key (stable, deterministic)."""
        mock_settings.return_value = SimpleNamespace(devnest_autoscaler_min_ec2_nodes_before_reclaim=2)

        nodes = [_ec2_node("ec2-b"), _ec2_node("ec2-a"), _ec2_node("ec2-c")]
        session = MagicMock()

        def _exec(_stmt: object) -> MagicMock:
            m = MagicMock()
            m.all = lambda: nodes
            return m

        session.exec.side_effect = _exec
        ev = evaluate_scale_down(session)
        assert ev.node_key == "ec2-a", "stable alphabetical tiebreak when capacity equal"

    @patch(
        "app.services.autoscaler_service.service._workload_counts_by_node_keys",
        return_value={"busy": 3},
    )
    @patch("app.services.autoscaler_service.service.count_ec2_ready_schedulable", return_value=2)
    @patch("app.services.autoscaler_service.service.get_settings")
    def test_no_reclaim_when_all_nodes_have_workloads(
        self, mock_settings: MagicMock, _n_ready: MagicMock, _counts: MagicMock
    ) -> None:
        mock_settings.return_value = SimpleNamespace(devnest_autoscaler_min_ec2_nodes_before_reclaim=2)

        n = _ec2_node("busy")
        session = MagicMock()

        def _exec(_stmt: object) -> MagicMock:
            m = MagicMock()
            m.all = lambda: [n]
            return m

        session.exec.side_effect = _exec
        ev = evaluate_scale_down(session)
        assert ev.node_key is None
        assert "no idle" in ev.reason

    @patch("app.services.autoscaler_service.service.count_ec2_ready_schedulable", return_value=1)
    @patch("app.services.autoscaler_service.service.get_settings")
    def test_no_reclaim_below_minimum_ready(
        self, mock_settings: MagicMock, _n_ready: MagicMock
    ) -> None:
        mock_settings.return_value = SimpleNamespace(devnest_autoscaler_min_ec2_nodes_before_reclaim=2)
        ev = evaluate_scale_down(MagicMock())
        assert ev.node_key is None
        assert "below minimum" in ev.reason


# ---------------------------------------------------------------------------
# Scale-down suppression log event
# ---------------------------------------------------------------------------

class TestScaleDownSuppressedLog:
    @patch(
        "app.services.autoscaler_service.service._workload_counts_by_node_keys",
        return_value={"node-a": 1},
    )
    @patch("app.services.autoscaler_service.service.count_ec2_ready_schedulable", return_value=3)
    @patch("app.services.autoscaler_service.service.get_settings")
    def test_suppressed_log_event_emitted(
        self, mock_settings: MagicMock, _n_ready: MagicMock, _counts: MagicMock
    ) -> None:
        mock_settings.return_value = SimpleNamespace(devnest_autoscaler_min_ec2_nodes_before_reclaim=2)

        n = _ec2_node("node-a")
        session = MagicMock()

        def _exec(_stmt: object) -> MagicMock:
            m = MagicMock()
            m.all = lambda: [n]
            return m

        session.exec.side_effect = _exec

        with patch("app.services.autoscaler_service.service.log_event") as mock_log:
            ev = evaluate_scale_down(session)

        assert ev.node_key is None
        from app.libs.observability.log_events import LogEvent
        logged_events = [c.args[1] for c in mock_log.call_args_list]
        assert LogEvent.AUTOSCALER_SCALE_DOWN_SUPPRESSED in logged_events
