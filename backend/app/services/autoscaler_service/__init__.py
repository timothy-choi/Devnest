"""V1 fleet autoscaler: conservative EC2 scale-up on no capacity and idle node reclaim."""

from .models import ScaleDownEvaluation, ScaleUpEvaluation
from .service import (
    evaluate_scale_down,
    evaluate_scale_up,
    maybe_provision_on_no_schedulable_capacity,
    provision_capacity_if_needed,
    reclaim_one_idle_ec2_node,
    select_node_for_scale_down,
    workload_count_on_node,
)

__all__ = [
    "ScaleDownEvaluation",
    "ScaleUpEvaluation",
    "evaluate_scale_down",
    "evaluate_scale_up",
    "maybe_provision_on_no_schedulable_capacity",
    "provision_capacity_if_needed",
    "reclaim_one_idle_ec2_node",
    "select_node_for_scale_down",
    "workload_count_on_node",
]
