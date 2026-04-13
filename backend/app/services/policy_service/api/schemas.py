"""Policy admin API request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PolicyRulesSchema(BaseModel):
    """Structured rules dict for a policy.

    All fields are optional; absent fields impose no restriction.
    """

    allow_workspace_creation: bool = True
    allow_workspace_start: bool = True
    allow_snapshot_creation: bool = True
    allow_session_creation: bool = True
    allow_node_provisioning: bool = True
    allowed_runtime_images: list[str] | None = None
    require_private_workspaces: bool = False


class CreatePolicyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    policy_type: str = Field(description="PolicyType: system | user | workspace")
    scope_type: str = Field(description="ScopeType: global | user | workspace")
    scope_id: int | None = None
    rules: PolicyRulesSchema = Field(default_factory=PolicyRulesSchema)
    is_active: bool = True


class PatchPolicyRequest(BaseModel):
    description: str | None = None
    rules: PolicyRulesSchema | None = None
    is_active: bool | None = None


class PolicyResponse(BaseModel):
    policy_id: int
    name: str
    description: str | None
    policy_type: str
    scope_type: str
    scope_id: int | None
    rules_json: dict[str, Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PolicyListResponse(BaseModel):
    items: list[PolicyResponse]
    total: int
