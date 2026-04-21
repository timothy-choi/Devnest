"""Request/response models for internal execution-node / infrastructure routes."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.services.placement_service.models import ExecutionNode


class ExecutionNodeSummaryResponse(BaseModel):
    """JSON-safe view of an :class:`~app.services.placement_service.models.ExecutionNode`."""

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
            last_error_code=row.last_error_code,
            last_error_message=row.last_error_message,
        )


class NodeKeyOrIdBody(BaseModel):
    """Exactly one selector for an execution node row."""

    node_id: int | None = Field(default=None, description="ExecutionNode.id (PK)")
    node_key: str | None = Field(default=None, description="ExecutionNode.node_key")

    @model_validator(mode="after")
    def _one_selector(self) -> NodeKeyOrIdBody:
        if self.node_id is None and not (self.node_key and str(self.node_key).strip()):
            raise ValueError("provide node_id or non-empty node_key")
        return self


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
