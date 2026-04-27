"""Settings validation for autoscaler-related fields."""

from __future__ import annotations

from app.libs.common.config import Settings


def test_devnest_autoscaler_min_ec2_nodes_before_reclaim_coerces_below_two() -> None:
    s = Settings(
        database_url="sqlite://",
        devnest_autoscaler_min_ec2_nodes_before_reclaim=1,
    )
    assert s.devnest_autoscaler_min_ec2_nodes_before_reclaim == 2


def test_phase1_evaluate_only_autoscaler_defaults_are_safe() -> None:
    s = Settings(database_url="sqlite://")
    assert s.devnest_autoscaler_enabled is False
    assert s.devnest_autoscaler_evaluate_only is True
    assert s.devnest_autoscaler_min_nodes == 1
    assert s.devnest_autoscaler_max_nodes == 10
    assert s.devnest_autoscaler_min_idle_slots == 1
    assert s.devnest_autoscaler_scale_out_cooldown_seconds == 300
    assert s.devnest_autoscaler_scale_in_cooldown_seconds == 900
