"""Registry row for a DevNest execution node (Docker host today, EC2 later)."""

from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Float, Integer
from sqlmodel import Field, SQLModel

from .enums import ExecutionNodeProviderType, ExecutionNodeStatus


class ExecutionNode(SQLModel, table=True):
    """
    Control-plane catalog of **nodes** that can run workspace containers.

    Field mapping (V1):

    - **Primary key** ``id`` — stable control-plane identifier (use this for FKs in future phases).
    - **Placement identity** ``node_key`` — string wired into :class:`~app.services.workspace_service.models.WorkspaceRuntime.node_id` and the orchestrator/topology stack (same role as historical ``DEVNEST_NODE_ID``).
    - **Provider** — ``provider_type`` / ``provider_instance_id`` reserve space for EC2 instance IDs without provisioning logic yet.
    - **Capacity** — ``total_*`` vs ``allocatable_*`` support future reservation accounting; V1 policy filters on allocatable only.

    TODO: Node agent heartbeats, persistent CPU/RAM reservations, EC2 lifecycle sync.
    """

    __tablename__ = "execution_node"

    id: int | None = Field(default=None, primary_key=True)
    node_key: str = Field(max_length=128, unique=True, index=True)
    name: str = Field(default="", max_length=255)
    provider_type: str = Field(
        default=ExecutionNodeProviderType.LOCAL.value,
        max_length=32,
        index=True,
    )
    provider_instance_id: str | None = Field(default=None, max_length=255)
    hostname: str | None = Field(default=None, max_length=255)
    private_ip: str | None = Field(default=None, max_length=64)
    status: str = Field(
        default=ExecutionNodeStatus.READY.value,
        max_length=32,
        index=True,
    )
    schedulable: bool = Field(default=True, index=True)

    total_cpu: float = Field(default=4.0, sa_column=Column(Float, nullable=False))
    total_memory_mb: int = Field(default=8192)
    allocatable_cpu: float = Field(default=4.0, sa_column=Column(Float, nullable=False))
    allocatable_memory_mb: int = Field(default=8192)

    metadata_json: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )

    default_topology_id: int | None = Field(
        default=None,
        index=True,
        description="Optional topology id when placing (soft ref; null uses DEVNEST_TOPOLOGY_ID).",
    )

    last_heartbeat_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_error_code: str | None = Field(default=None, max_length=64)
    last_error_message: str | None = Field(default=None, max_length=4096)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
