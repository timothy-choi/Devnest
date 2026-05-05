"""Autoscaler decision records (serializable to internal API JSON)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FleetCapacitySnapshot:
    total_nodes: int
    ec2_nodes_active: int
    ready_schedulable_nodes: int
    ready_schedulable_ec2_nodes: int
    provisioning_nodes: int
    draining_nodes: int
    active_slots: int
    free_slots: int
    pending_workspace_jobs: int
    # Queued workspace jobs whose types imply placement demand (CREATE/START/…); not merged with failures.
    pending_placement_jobs: int
    # Raw audit count in the recent-activity window (see ``devnest_autoscaler_recent_activity_window_seconds``).
    recent_placement_failures: int
    # Failures counted toward scale-out demand (0 when raw failures <= 1 to ignore transients).
    filtered_placement_failure_signals: int
    # Queued placement jobs + filtered failure signals (workspace-unit demand estimate).
    pending_demand_workspace_units: int
    # Estimated additional workspaces schedulable on READY pool from free slots vs default request shape.
    ready_workspace_capacity: int
    # Sum of estimated empty capacity on EC2 nodes in PROVISIONING (incoming capacity).
    provisioning_workspace_capacity: int
    # Incoming + ready estimated workspace slots (placement demand compared against this).
    total_available_workspace_capacity: int
    total_allocatable_cpu: float
    free_cpu: float
    total_allocatable_memory_mb: int
    free_memory_mb: int
    total_allocatable_disk_mb: int
    free_disk_mb: int
    idle_ec2_node_count: int


@dataclass(frozen=True)
class FleetAutoscalerDecision:
    action: str
    scale_out_recommended: bool
    scale_in_recommended: bool
    no_action: bool
    suppressed_by_cooldown: bool
    suppressed_by_cap: bool
    suppressed_by_config: bool
    suppressed_by_recent_provisioning: bool
    reasons: list[str]
    capacity: FleetCapacitySnapshot
    min_nodes: int
    max_nodes: int
    min_idle_slots: int
    max_concurrent_provisioning: int
    scale_out_cooldown_seconds: int
    scale_in_cooldown_seconds: int
    evaluate_only: bool
    enabled: bool
    # Latest scale-down evaluation (idle candidate); ``node_key`` is None when none.
    scale_down: ScaleDownEvaluation


@dataclass(frozen=True)
class ScaleUpEvaluation:
    should_provision: bool
    reason: str
    provisioning_in_flight: int
    # Number of READY+schedulable EC2 nodes with zero active workloads at evaluation time.
    # Non-zero means scale-up was or should be suppressed (prefer reuse over provisioning).
    idle_ec2_node_count: int = field(default=0)


@dataclass(frozen=True)
class ScaleDownEvaluation:
    """Candidate for reclaim; ``node_key`` empty when none."""

    node_key: str | None
    reason: str
    idle_ec2_ready_nodes: int
    min_ec2_nodes_before_reclaim: int
