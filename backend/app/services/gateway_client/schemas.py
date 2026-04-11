"""JSON bodies for POST /routes (see devnest-gateway/route_admin/route_admin_app.py)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GatewayRouteRegisterPayload(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=128)
    public_host: str = Field(min_length=1, max_length=512)
    target: str = Field(min_length=1, max_length=1024)
