"""JSON bodies for POST /routes (see devnest-gateway/route_admin/route_admin_app.py)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GatewayRouteRegisterPayload(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=128)
    public_host: str = Field(min_length=1, max_length=512)
    target: str = Field(min_length=1, max_length=1024)
    node_key: str | None = Field(default=None, max_length=256)
    execution_node_id: int | None = Field(default=None, ge=1)
