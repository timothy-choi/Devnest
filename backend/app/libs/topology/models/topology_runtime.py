"""Runtime state for an instantiated topology on one execution node."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime
from sqlmodel import Field, SQLModel

from .enums import TopologyRuntimeStatus


class TopologyRuntime(SQLModel, table=True):
    """
    Observed / agent-reported state for ``topology`` on ``node_id`` (bridge, CIDR, NAT flags, etc.).
    """

    __tablename__ = "topology_runtime"

    topology_runtime_id: int | None = Field(default=None, primary_key=True)
    topology_id: int = Field(foreign_key="topology.topology_id", index=True)
    node_id: str = Field(index=True, max_length=128)
    status: str = Field(max_length=32, default=TopologyRuntimeStatus.READY.value, index=True)
    bridge_name: str | None = Field(default=None, max_length=64)
    cidr: str | None = Field(default=None, max_length=64)
    gateway_ip: str | None = Field(default=None, max_length=64)
    nat_enabled: bool | None = Field(default=None, sa_column=Column(Boolean, nullable=True))
    iptables_profile: str | None = Field(default=None, max_length=128)
    managed_by_agent: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    last_checked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_error_code: str | None = Field(default=None, max_length=64)
    last_error_message: str | None = Field(default=None, max_length=2048)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
