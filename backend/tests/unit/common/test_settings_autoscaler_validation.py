"""Settings validation for autoscaler-related fields."""

from __future__ import annotations

from app.libs.common.config import Settings


def test_devnest_autoscaler_min_ec2_nodes_before_reclaim_coerces_below_two() -> None:
    s = Settings(
        database_url="sqlite://",
        devnest_autoscaler_min_ec2_nodes_before_reclaim=1,
    )
    assert s.devnest_autoscaler_min_ec2_nodes_before_reclaim == 2
