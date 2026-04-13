"""Quota admin API request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CreateQuotaRequest(BaseModel):
    scope_type: str = Field(description="ScopeType: global | user | workspace")
    scope_id: int | None = Field(default=None, description="user_id or workspace_id; None for global")
    max_workspaces: int | None = Field(default=None, ge=0)
    max_running_workspaces: int | None = Field(default=None, ge=0)
    max_cpu: float | None = Field(default=None, ge=0)
    max_memory_mb: int | None = Field(default=None, ge=0)
    max_storage_mb: int | None = Field(default=None, ge=0)
    max_sessions: int | None = Field(default=None, ge=0)
    max_snapshots: int | None = Field(default=None, ge=0)
    max_runtime_hours: float | None = Field(default=None, ge=0)


class PatchQuotaRequest(BaseModel):
    """All fields optional — only provided fields are updated."""

    max_workspaces: int | None = Field(default=None, ge=0)
    max_running_workspaces: int | None = Field(default=None, ge=0)
    max_cpu: float | None = Field(default=None, ge=0)
    max_memory_mb: int | None = Field(default=None, ge=0)
    max_storage_mb: int | None = Field(default=None, ge=0)
    max_sessions: int | None = Field(default=None, ge=0)
    max_snapshots: int | None = Field(default=None, ge=0)
    max_runtime_hours: float | None = Field(default=None, ge=0)


class QuotaResponse(BaseModel):
    quota_id: int
    scope_type: str
    scope_id: int | None
    max_workspaces: int | None
    max_running_workspaces: int | None
    max_cpu: float | None
    max_memory_mb: int | None
    max_storage_mb: int | None
    max_sessions: int | None
    max_snapshots: int | None
    max_runtime_hours: float | None
    created_at: datetime
    updated_at: datetime


class QuotaListResponse(BaseModel):
    items: list[QuotaResponse]
    total: int
