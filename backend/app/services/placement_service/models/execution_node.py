"""Registry row for a DevNest execution node (Docker host today, EC2 later)."""

from datetime import datetime, timezone

from sqlalchemy import JSON, CheckConstraint, Column, DateTime, Float, Integer
from sqlmodel import Field, SQLModel

from .enums import ExecutionNodeExecutionMode, ExecutionNodeProviderType, ExecutionNodeStatus


class ExecutionNode(SQLModel, table=True):
    """
    Control-plane catalog of **nodes** that can run workspace containers.

    Field mapping (V1):

    - **Primary key** ``id`` — stable control-plane identifier (use this for FKs in future phases).
    - **Placement identity** ``node_key`` — string wired into :class:`~app.services.workspace_service.models.WorkspaceRuntime.node_id` and the orchestrator/topology stack (same role as historical ``DEVNEST_NODE_ID``).
    - **Provider** — ``provider_type`` / ``provider_instance_id`` reserve space for EC2 instance IDs without provisioning logic yet.
    - **Capacity** — ``total_*`` vs ``allocatable_*`` support future reservation accounting; V1 policy filters on allocatable only.

    **Status vs schedulable:** only ``READY`` + ``schedulable=True`` are candidates for V1 placement.
    ``NOT_READY`` / ``DRAINING`` are excluded until an operator or agent transitions them.

    **Capacity:** ``allocatable_*`` must not exceed ``total_*`` (enforced at DB layer). V1 placement
    filters on allocatable only and does not decrement it — concurrent workspaces can exceed
    real capacity until reservation accounting lands (TODO).

    **Execution:** ``execution_mode`` selects how :mod:`app.services.node_execution_service` builds
    a Docker client and Linux command runner. ``LOCAL_DOCKER`` uses the worker process environment
    (``docker.from_env()``).     ``SSH_DOCKER`` uses Docker's ``ssh://`` transport to the daemon. The SSH target is resolved in
    order: ``ssh_host``, then ``hostname``, then ``private_ip`` (useful once EC2 sets private IP).
    Requires SSH keys in the worker environment; ``paramiko`` for docker-py. Topology bridge/veth
    commands run on the same host as the daemon via the SSH-backed runner. ``ssh_*`` and IP fields
    are ignored for ``LOCAL_DOCKER``.

    When ``provider_type=ec2``, ``provider_instance_id`` is the instance id. ``region``,
    ``availability_zone``, ``instance_type``, ``public_ip``, ``iam_instance_profile_name``, and
    ``last_synced_at`` are filled by :mod:`app.services.providers.ec2_provider` (no provisioning).

    TODO: Node agent heartbeats, persistent CPU/RAM reservations, SSM transport, auto sync on events.
    """

    __tablename__ = "execution_node"
    __table_args__ = (
        CheckConstraint("allocatable_cpu >= 0", name="ck_exec_node_alloc_cpu_nonneg"),
        CheckConstraint("total_cpu > 0", name="ck_exec_node_total_cpu_pos"),
        CheckConstraint("allocatable_cpu <= total_cpu", name="ck_exec_node_cpu_alloc_lte_total"),
        CheckConstraint("allocatable_memory_mb >= 0", name="ck_exec_node_alloc_mem_nonneg"),
        CheckConstraint("total_memory_mb > 0", name="ck_exec_node_total_mem_pos"),
        CheckConstraint(
            "allocatable_memory_mb <= total_memory_mb",
            name="ck_exec_node_mem_alloc_lte_total",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    node_key: str = Field(max_length=128, unique=True, index=True)
    name: str = Field(default="", max_length=255)
    provider_type: str = Field(
        default=ExecutionNodeProviderType.LOCAL.value,
        max_length=32,
        index=True,
    )
    provider_instance_id: str | None = Field(default=None, max_length=255, index=True)
    region: str | None = Field(
        default=None,
        max_length=32,
        description="Cloud region (e.g. AWS region) for EC2-backed nodes.",
    )
    availability_zone: str | None = Field(default=None, max_length=32)
    instance_type: str | None = Field(default=None, max_length=64)
    hostname: str | None = Field(default=None, max_length=255)
    private_ip: str | None = Field(
        default=None,
        max_length=64,
        description="Optional; used as ssh_docker connect target when ssh_host and hostname are unset.",
    )
    public_ip: str | None = Field(default=None, max_length=64)
    iam_instance_profile_name: str | None = Field(
        default=None,
        max_length=255,
        description="EC2 IAM instance profile name (not ARN), when attached.",
    )

    execution_mode: str = Field(
        default=ExecutionNodeExecutionMode.LOCAL_DOCKER.value,
        max_length=32,
        index=True,
        description="local_docker | ssh_docker — see ExecutionNodeExecutionMode.",
    )
    ssh_host: str | None = Field(default=None, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str | None = Field(default=None, max_length=64)

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

    last_synced_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
        description="Last successful EC2 describe / registry sync (control plane).",
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
