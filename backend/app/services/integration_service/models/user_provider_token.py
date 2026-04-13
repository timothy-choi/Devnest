"""Persistent, encrypted OAuth provider token for a DevNest user.

Stores the provider access token (and optional refresh token) needed for
workspace-scoped operations such as repository cloning and CI/CD triggers.

Tokens are encrypted at rest using Fernet symmetric encryption; see
:mod:`app.services.integration_service.token_crypto`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


class UserProviderToken(SQLModel, table=True):
    __tablename__ = "user_provider_token"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_provider_token"),
    )

    token_id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user_auth.user_auth_id", index=True)

    # Provider identifier: "github" | "google"
    provider: str = Field(max_length=32, index=True)

    # Fernet-encrypted provider access token.
    access_token_encrypted: str = Field(max_length=2048)

    # Fernet-encrypted provider refresh token (nullable; GitHub issues long-lived tokens).
    refresh_token_encrypted: str | None = Field(default=None, max_length=2048)

    # Space-separated list of granted OAuth scopes.
    scopes: str = Field(default="", max_length=512)

    # Provider's opaque user identifier.
    provider_user_id: str = Field(max_length=255)
    # Provider's human-readable username / login.
    provider_username: str | None = Field(default=None, max_length=255)

    # Optional expiry timestamp from the provider (null = non-expiring, e.g. GitHub classic PAT).
    expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
