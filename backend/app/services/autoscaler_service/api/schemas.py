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


class AutoscalerEvaluateResponse(BaseModel):
    scale_up: ScaleUpEvaluationResponse
    scale_down: ScaleDownEvaluationResponse


class ProvisionOneResponse(BaseModel):
    provisioned: bool
    evaluation: ScaleUpEvaluationResponse
    node_key: str | None = None
    instance_id: str | None = None


class ReclaimOneResponse(BaseModel):
    reclaimed: bool
    node_key: str | None = None
    reason: str | None = None
