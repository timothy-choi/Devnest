"""Fleet autoscaler: evaluate capacity, perform safe EC2 scale-out, and expose manual reclaim tools."""

from .models import FleetAutoscalerDecision, FleetCapacitySnapshot, ScaleDownEvaluation, ScaleUpEvaluation
from .service import (
    evaluate_fleet_autoscaler_tick,
    evaluate_scale_down,
    evaluate_scale_up,
    maybe_provision_on_no_schedulable_capacity,
    provision_capacity_if_needed,
    provision_one_from_fleet_decision,
    reclaim_one_idle_ec2_node,
    record_placement_failed_scale_out_signal,
    run_scale_out_tick,
    select_node_for_scale_down,
    workload_count_on_node,
)

__all__ = [
    "FleetAutoscalerDecision",
    "FleetCapacitySnapshot",
    "ScaleDownEvaluation",
    "ScaleUpEvaluation",
    "evaluate_fleet_autoscaler_tick",
    "evaluate_scale_down",
    "evaluate_scale_up",
    "maybe_provision_on_no_schedulable_capacity",
    "provision_capacity_if_needed",
    "provision_one_from_fleet_decision",
    "reclaim_one_idle_ec2_node",
    "record_placement_failed_scale_out_signal",
    "run_scale_out_tick",
    "select_node_for_scale_down",
    "workload_count_on_node",
]
