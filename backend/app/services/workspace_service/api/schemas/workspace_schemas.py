"""Workspace API request/response models (V1 control-plane)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PortMappingSchema(BaseModel):
    """Host/container port pair for ``config_json``."""

    container_port: int = Field(..., ge=1, le=65535)
    host_port: int | None = Field(default=None, ge=1, le=65535)


class WorkspaceRuntimeSpecSchema(BaseModel):
    """Intent bundled into ``WorkspaceConfig.config_json`` (no runtime execution here)."""

    image: str | None = Field(default=None, max_length=512)
    cpu_limit_cores: float | None = Field(default=None, gt=0)
    memory_limit_mib: int | None = Field(default=None, gt=0)
    env: dict[str, str] = Field(default_factory=dict)
    ports: list[PortMappingSchema] = Field(default_factory=list)
    topology_id: int | None = None
    storage: dict[str, Any] = Field(default_factory=dict)

    def to_config_dict(self) -> dict[str, Any]:
        return {
            "image": self.image,
            "cpu_limit_cores": self.cpu_limit_cores,
            "memory_limit_mib": self.memory_limit_mib,
            "env": self.env,
            "ports": [p.model_dump() for p in self.ports],
            "topology_id": self.topology_id,
            "storage": self.storage,
        }


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(..., max_length=255)
    description: str | None = Field(default=None, max_length=8192)
    is_private: bool = True
    runtime: WorkspaceRuntimeSpecSchema = Field(
        default_factory=WorkspaceRuntimeSpecSchema,
        description="Seeds WorkspaceConfig v1 JSON.",
    )

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("name must not be empty")
        return s


class CreateWorkspaceAcceptedResponse(BaseModel):
    """202-style acceptance: persistence done; provisioning is asynchronous."""

    workspace_id: int
    status: str
    config_version: int
    job_id: int
    message: str = "Workspace creation accepted."


class WorkspaceIntentAcceptedResponse(BaseModel):
    """202-style acceptance for start/stop/restart/delete/update intent requests."""

    workspace_id: int
    status: str
    job_id: int
    job_type: str
    requested_config_version: int
    message: str = "Workspace request accepted."
    issues: list[str] = Field(default_factory=list)


class PatchWorkspaceUpdateRequest(BaseModel):
    """Intent to roll forward config: new ``WorkspaceConfig`` row at ``latest + 1`` (service-computed)."""

    runtime: WorkspaceRuntimeSpecSchema = Field(
        ...,
        description="Next config payload; persisted as the next WorkspaceConfig version.",
    )


class WorkspaceSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workspace_id: int
    name: str
    status: str
    is_private: bool
    created_at: datetime


class WorkspaceListResponse(BaseModel):
    items: list[WorkspaceSummaryResponse]
    total: int


class WorkspaceDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workspace_id: int
    name: str
    description: str | None
    owner_user_id: int
    status: str
    status_reason: str | None
    last_error_code: str | None
    last_error_message: str | None
    endpoint_ref: str | None
    public_host: str | None
    active_sessions_count: int
    is_private: bool
    created_at: datetime
    updated_at: datetime
    last_started: datetime | None
    last_stopped: datetime | None
    latest_config_version: int | None = None
