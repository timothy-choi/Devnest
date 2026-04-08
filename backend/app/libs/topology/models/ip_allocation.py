"""Stable internal IP lease per (node, topology, workspace)."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


class IpAllocation(SQLModel, table=True):
    """
    V1: one row per (``node_id``, ``topology_id``, ``workspace_id``) — update ``ip`` in place; set
    ``released_at`` to release. Uniqueness prevents parallel duplicate leases for the same triple.
    """

    __tablename__ = "ip_allocation"
    __table_args__ = (
        UniqueConstraint("node_id", "topology_id", "workspace_id", name="uq_ip_allocation_node_topology_workspace"),
    )

    ip_allocation_id: int | None = Field(default=None, primary_key=True)
    node_id: str = Field(index=True, max_length=128)
    topology_id: int = Field(foreign_key="topology.topology_id", index=True)
    workspace_id: int = Field(index=True)
    ip: str = Field(max_length=64, index=True)
    leased_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    released_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
