"""Audit log API response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    audit_log_id: int
    actor_user_id: int | None
    actor_type: str
    action: str
    resource_type: str
    resource_id: str | None
    workspace_id: int | None
    job_id: int | None
    node_id: str | None
    outcome: str
    reason: str | None
    metadata: dict[str, Any] | None
    correlation_id: str | None
    created_at: datetime


class AuditLogListResponse(BaseModel):
    items: list[AuditLogResponse]
    total: int
