"""Desired topology specification (versioned JSON spec for additive evolution)."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, JSON
from sqlmodel import Field, SQLModel


class Topology(SQLModel, table=True):
    """
    Declarative topology template: mode, egress, policy, services/devices live in ``spec_json``.

    Outer columns stay stable across V1+; new capabilities extend ``spec_json`` without renames.

    ``version`` is a **string spec label** (e.g. ``\"v1\"``, ``\"1.0.0\"``): human- or tooling-defined,
    not a database auto-increment. Use integers inside ``spec_json`` only if you need numeric counters.
    """

    __tablename__ = "topology"

    topology_id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, max_length=255)
    version: str = Field(index=True, max_length=32)
    spec_json: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
