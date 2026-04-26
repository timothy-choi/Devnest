"""Request/response models for internal execution-node / infrastructure routes."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, model_validator
from sqlmodel import Session

from app.services.placement_service.capacity import count_active_workloads_on_node_key
from app.services.placement_service.models import ExecutionNode


class ExecutionNodeSummaryResponse(BaseModel):
    """JSON-safe view of an :class:`~app.services.placement_service.models.ExecutionNode`.

    Excludes ``metadata_json`` and SSH/SSM connection fields so internal listings are not a
    secret/config dump.
    """

    id: int | None
    node_key: str
    name: str
    provider_type: str
    provider_instance_id: str | None = None
    region: str | None = None
    execution_mode: str
    status: str
    schedulable: bool
    max_workspaces: int
    allocatable_disk_mb: int
    instance_type: str | None = None
    private_ip: str | None = None
    public_ip: str | None = None
    hostname: str | None = None
    last_heartbeat_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None

    @classmethod
    def from_row(cls, row: ExecutionNode) -> ExecutionNodeSummaryResponse:
        return cls(
            id=row.id,
            node_key=row.node_key,
            name=row.name,
            provider_type=row.provider_type,
            provider_instance_id=row.provider_instance_id,
            region=row.region,
            execution_mode=row.execution_mode,
            status=row.status,
            schedulable=row.schedulable,
            max_workspaces=int(row.max_workspaces or 0),
            allocatable_disk_mb=int(row.allocatable_disk_mb or 0),
            instance_type=row.instance_type,
            private_ip=row.private_ip,
            public_ip=row.public_ip,
            hostname=row.hostname,
            last_heartbeat_at=row.last_heartbeat_at,
            last_error_code=row.last_error_code,
            last_error_message=row.last_error_message,
        )


class ExecutionNodeCapacityResponse(ExecutionNodeSummaryResponse):
    """Execution node summary plus slot accounting (same cohort as placement capacity)."""

    active_workspace_slots: int = Field(
        ...,
        ge=0,
        description="RUNNING (etc.) workspaces pinned to this node_key that consume a schedulable slot.",
    )
    available_workspace_slots: int = Field(..., ge=0, description="max(0, max_workspaces - active).")
    heartbeat_age_seconds: int | None = Field(
        default=None,
        description="Seconds since last_heartbeat_at (UTC) when set; null if never heartbeated.",
    )

    @classmethod
    def from_row_with_capacity(cls, session: Session, row: ExecutionNode) -> ExecutionNodeCapacityResponse:
        base = ExecutionNodeSummaryResponse.from_row(row)
        key = (row.node_key or "").strip()
        active = count_active_workloads_on_node_key(session, key)
        max_w = int(row.max_workspaces or 0)
        avail = max(0, max_w - active)
        now = datetime.now(timezone.utc)
        hb_age: int | None = None
        if row.last_heartbeat_at is not None:
            delta = now - row.last_heartbeat_at
            sec = int(delta.total_seconds())
            hb_age = max(0, sec)
        return cls(
            **base.model_dump(),
            active_workspace_slots=active,
            available_workspace_slots=avail,
            heartbeat_age_seconds=hb_age,
        )


class WorkspaceOnNodeBrief(BaseModel):
    """Minimal workspace row for ops listing (no secrets)."""

    workspace_id: int
    name: str
    status: str


class NodeWorkspacesSummaryResponse(BaseModel):
    """Workspaces whose ``workspace_runtime.node_id`` matches this catalog ``node_key``."""

    node_key: str
    execution_node_id: int | None = None
    workspace_count: int = Field(..., ge=0)
    workspaces: list[WorkspaceOnNodeBrief] = Field(default_factory=list)


class NodeKeyOrIdBody(BaseModel):
    """Exactly one selector for an execution node row."""

    node_id: int | None = Field(default=None, description="ExecutionNode.id (PK)")
    node_key: str | None = Field(default=None, description="ExecutionNode.node_key")

    @model_validator(mode="after")
    def _one_selector(self) -> NodeKeyOrIdBody:
        if self.node_id is None and not (self.node_key and str(self.node_key).strip()):
            raise ValueError("provide node_id or non-empty node_key")
        return self


class ExecutionNodeHeartbeatRequest(BaseModel):
    """Agent / worker payload for ``POST /internal/execution-nodes/heartbeat``."""

    node_key: str = Field(..., min_length=1, max_length=128, description="ExecutionNode.node_key")
    docker_ok: bool
    disk_free_mb: int = Field(..., ge=0, le=2_000_000_000)
    slots_in_use: int = Field(..., ge=0, le=1_000_000)
    version: str = Field(..., min_length=1, max_length=128)


class ExecutionNodeHeartbeatResponse(BaseModel):
    """Minimal response for heartbeat POST (Phase 3a)."""

    id: int | None
    node_key: str
    status: str
    schedulable: bool
    last_heartbeat_at: datetime | None

    @classmethod
    def from_row(cls, row: ExecutionNode) -> ExecutionNodeHeartbeatResponse:
        return cls(
            id=row.id,
            node_key=row.node_key,
            status=row.status,
            schedulable=bool(row.schedulable),
            last_heartbeat_at=row.last_heartbeat_at,
        )


class ProvisionExecutionNodeRequest(BaseModel):
    """Optional overrides; omitted fields fall back to ``DEVNEST_EC2_*`` / ``AWS_REGION`` settings."""

    ami_id: str | None = None
    instance_type: str | None = None
    subnet_id: str | None = None
    security_group_ids: list[str] | None = None
    iam_instance_profile_name: str | None = None
    key_name: str | None = None
    region: str | None = None
    node_key: str | None = None
    name_tag: str | None = None
    execution_mode: str | None = None
    ssh_user: str | None = None
    extra_tags: dict[str, str] | None = None
    wait_until_running: bool = True


class RegisterExistingEc2Body(BaseModel):
    instance_id: str = Field(..., min_length=1)
    node_key: str | None = None
    ssh_user: str | None = None
    execution_mode: str | None = Field(default=None, description="ssm_docker or ssh_docker")


class SyncExecutionNodeBody(NodeKeyOrIdBody):
    promote_provisioning_when_ready: bool = True


class ExecutionNodeSmokeResponse(BaseModel):
    """Sanitized result of ``POST /internal/execution-nodes/smoke-read-only`` (Phase 3b Step 6)."""

    ok: bool
    node_key: str
    execution_mode: str
    schedulable: bool
    status: str
    command_status: str = Field(..., description="Success, Failed, or Skipped")
    output_preview: str = Field(default="", max_length=2500, description="Truncated stdout/stderr or error text")
    provider_instance_id: str | None = None
