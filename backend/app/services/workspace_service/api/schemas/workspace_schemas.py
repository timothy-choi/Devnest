"""Workspace API request/response models (V1 control-plane)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PortMappingSchema(BaseModel):
    """Host/container port pair for ``config_json``."""

    container_port: int = Field(..., ge=1, le=65535)
    host_port: int | None = Field(default=None, ge=1, le=65535)


class WorkspaceFeatureFlags(BaseModel):
    """Optional feature gates stored in ``config_json.features``.

    All features default to ``False`` (disabled). Users must explicitly opt in at
    workspace creation or update time. Feature-disabled workspaces reject attempts
    to use that capability with an explicit 409/403.

    Known feature keys
    ------------------
    terminal_enabled   — allow WS terminal (/workspaces/{id}/terminal).
    ci_enabled         — CI/CD integration (future; currently reserved).
    ai_tools_enabled   — AI assistant tooling (future; currently reserved).
    """

    terminal_enabled: bool = False
    ci_enabled: bool = False
    ai_tools_enabled: bool = False

    model_config = ConfigDict(extra="allow")


def get_workspace_features(config_json: dict | None) -> WorkspaceFeatureFlags:
    """Parse feature flags from a ``WorkspaceConfig.config_json`` dict.

    Unknown keys are preserved (``extra="allow"``) so future feature additions
    are forward-compatible with older code reading the config.
    Returns a fully-defaulted ``WorkspaceFeatureFlags`` when ``config_json`` is
    missing or has no ``features`` key.
    """
    raw = (config_json or {}).get("features") or {}
    if not isinstance(raw, dict):
        raw = {}
    return WorkspaceFeatureFlags.model_validate(raw)


class WorkspaceRuntimeSpecSchema(BaseModel):
    """Intent bundled into ``WorkspaceConfig.config_json`` (no runtime execution here)."""

    image: str | None = Field(default=None, max_length=512)
    cpu_limit_cores: float | None = Field(default=None, gt=0)
    memory_limit_mib: int | None = Field(default=None, gt=0)
    env: dict[str, str] = Field(default_factory=dict)
    ports: list[PortMappingSchema] = Field(default_factory=list)
    topology_id: int | None = None
    storage: dict[str, Any] = Field(default_factory=dict)
    features: WorkspaceFeatureFlags = Field(
        default_factory=WorkspaceFeatureFlags,
        description=(
            "Optional feature gates. Disabled by default. "
            "Set terminal_enabled=true to allow terminal WebSocket access."
        ),
    )

    def to_config_dict(self) -> dict[str, Any]:
        return {
            "image": self.image,
            "cpu_limit_cores": self.cpu_limit_cores,
            "memory_limit_mib": self.memory_limit_mib,
            "env": self.env,
            "ports": [p.model_dump() for p in self.ports],
            "topology_id": self.topology_id,
            "storage": self.storage,
            "features": self.features.model_dump(),
        }


class WorkspaceAISecretInput(BaseModel):
    provider: Literal["openai", "anthropic"]
    api_key: str = Field(..., min_length=1, max_length=8192)


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(..., max_length=255)
    description: str | None = Field(default=None, max_length=8192)
    is_private: bool = True
    runtime: WorkspaceRuntimeSpecSchema = Field(
        default_factory=WorkspaceRuntimeSpecSchema,
        description="Seeds WorkspaceConfig v1 JSON.",
    )
    ai_secret: WorkspaceAISecretInput | None = Field(
        default=None,
        description="Optional encrypted AI provider key to store separately from runtime config.",
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


class WorkspaceSecretMutationResponse(BaseModel):
    workspace_id: int
    message: str


class WorkspaceIntentAcceptedResponse(BaseModel):
    """202-style acceptance for start/stop/restart/delete/update intent requests."""

    workspace_id: int
    status: str
    job_id: int
    job_type: str
    requested_config_version: int
    message: str = "Workspace request accepted."
    issues: list[str] = Field(default_factory=list)


class WorkspaceAttachRequest(BaseModel):
    """Optional metadata for POST /workspaces/attach/{id} (V1+ session row)."""

    client_metadata: dict[str, Any] = Field(default_factory=dict)


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


class WorkspaceAccessResponse(BaseModel):
    """Access coordinates when RUNNING, runtime ready, and a valid workspace session token is supplied."""

    workspace_id: int
    success: bool
    status: str
    runtime_ready: bool
    endpoint_ref: str | None = None
    public_host: str | None = None
    internal_endpoint: str | None = None
    gateway_url: str | None = Field(
        default=None,
        description="TODO: public URL via edge gateway when route registration exists; V1 returns null.",
    )
    issues: list[str] = Field(default_factory=list)


class WorkspaceAttachResponse(BaseModel):
    """Attach creates a workspace session when RUNNING + runtime ready; returns a one-time opaque token."""

    workspace_id: int
    accepted: bool
    status: str
    runtime_ready: bool
    active_sessions_count: int
    workspace_session_id: int
    session_token: str = Field(
        ...,
        description="Opaque bearer for X-DevNest-Workspace-Session on GET /workspaces/{id}/access. Shown once.",
    )
    session_expires_at: datetime
    endpoint_ref: str | None = None
    public_host: str | None = None
    internal_endpoint: str | None = None
    gateway_url: str | None = Field(
        default=None,
        description="TODO: public URL via edge gateway when route registration exists; V1 returns null.",
    )
    issues: list[str] = Field(default_factory=list)


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
    reopen_issues: list[str] = Field(
        default_factory=list,
        description="Control-plane checks (host/path drift vs current settings) that block a safe reopen.",
    )
    restorable_snapshot_count: int = Field(
        default=0,
        ge=0,
        description="Number of AVAILABLE snapshots for this workspace (restore path when project data is missing).",
    )
