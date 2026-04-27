"""Pydantic schemas for internal autoscaler routes."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ScaleUpEvaluationResponse(BaseModel):
    should_provision: bool
    reason: str
    provisioning_in_flight: int
    idle_ec2_node_count: int = 0


class ScaleDownEvaluationResponse(BaseModel):
    node_key: str | None
    reason: str
    idle_ec2_ready_nodes: int


class FleetCapacitySnapshotResponse(BaseModel):
    total_nodes: int
    ec2_nodes_active: int
    ready_schedulable_nodes: int
    ready_schedulable_ec2_nodes: int
    provisioning_nodes: int
    draining_nodes: int
    active_slots: int
    free_slots: int
    pending_workspace_jobs: int
    pending_placement_jobs: int
    total_allocatable_cpu: float
    free_cpu: float
    total_allocatable_memory_mb: int
    free_memory_mb: int
    total_allocatable_disk_mb: int
    free_disk_mb: int
    idle_ec2_node_count: int


class FleetAutoscalerDecisionResponse(BaseModel):
    action: str = Field(
        description="One of scale_out_recommended, scale_in_recommended, no_action, "
        "suppressed_by_cooldown, suppressed_by_cap, suppressed_by_config.",
    )
    scale_out_recommended: bool
    scale_in_recommended: bool
    no_action: bool
    suppressed_by_cooldown: bool
    suppressed_by_cap: bool
    suppressed_by_config: bool
    reasons: list[str]
    capacity: FleetCapacitySnapshotResponse
    min_nodes: int
    max_nodes: int
    min_idle_slots: int
    max_concurrent_provisioning: int
    scale_out_cooldown_seconds: int
    scale_in_cooldown_seconds: int
    evaluate_only: bool
    enabled: bool


class AutoscalerEvaluateResponse(BaseModel):
    scale_up: ScaleUpEvaluationResponse
    scale_down: ScaleDownEvaluationResponse
    decision: FleetAutoscalerDecisionResponse


class ProvisionOneResponse(BaseModel):
    provisioned: bool
    evaluation: ScaleUpEvaluationResponse
    node_key: str | None = None
    instance_id: str | None = None


class ReclaimOneResponse(BaseModel):
    reclaimed: bool
    node_key: str | None = None
    reason: str | None = None
