"""Usage tracking API response schemas."""

from __future__ import annotations

from pydantic import BaseModel


class WorkspaceUsageSummaryResponse(BaseModel):
    workspace_id: int
    owner_user_id: int
    totals: dict[str, int]


class UserUsageSummaryResponse(BaseModel):
    owner_user_id: int
    totals_by_event: dict[str, int]
