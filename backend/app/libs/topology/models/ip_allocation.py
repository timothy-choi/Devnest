"""Stable internal IP lease per (node, topology, workspace)."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel


class IpAllocation(SQLModel, table=True):
    """
    V1: one row per (``node_id``, ``topology_id``, ``workspace_id``) — update ``ip`` in place; set
    ``released_at`` to release.

    Uniqueness:

    - ``(node_id, topology_id, workspace_id)``: one lease record per workspace (concurrent allocate
      for the same workspace cannot create duplicate rows).
    - Active ``(topology_id, node_id, ip)`` (where ``released_at IS NULL``): no two concurrent
      leases on the same node share the same IP; released rows are excluded so addresses can be reused.
    """

    __tablename__ = "ip_allocation"
    __table_args__ = (
        UniqueConstraint("node_id", "topology_id", "workspace_id", name="uq_ip_allocation_node_topology_workspace"),
        Index(
            "uq_ip_allocation_active_topology_node_ip",
            "topology_id",
            "node_id",
            "ip",
            unique=True,
            sqlite_where=text("released_at IS NULL"),
            postgresql_where=text("released_at IS NULL"),
        ),
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
