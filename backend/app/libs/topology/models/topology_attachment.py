"""One workspace network attachment to one topology on one node."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

from .enums import TopologyAttachmentStatus


class TopologyAttachment(SQLModel, table=True):
    """
    Attachment row: Docker ``container_id``, stable ``workspace_ip``, veth/bridge identifiers.

    ``workspace_id`` is the logical workspace id (FK to a future ``workspace`` table when it exists).
    """

    __tablename__ = "topology_attachment"

    attachment_id: int | None = Field(default=None, primary_key=True)
    topology_id: int = Field(foreign_key="topology.topology_id", index=True)
    node_id: str = Field(index=True, max_length=128)
    workspace_id: int = Field(index=True)
    container_id: str | None = Field(default=None, max_length=128, index=True)
    status: str = Field(max_length=32, default=TopologyAttachmentStatus.ATTACHING.value, index=True)
    workspace_ip: str | None = Field(default=None, max_length=64)
    interface_host: str | None = Field(default=None, max_length=64)
    interface_container: str | None = Field(default=None, max_length=64)
    bridge_name: str | None = Field(default=None, max_length=64)
    gateway_ip: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
