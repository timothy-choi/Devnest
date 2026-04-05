"""User/profile API responses."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MyProfileResponse(BaseModel):
    """Full profile for the authenticated user (no auth secrets)."""

    model_config = ConfigDict(from_attributes=True)

    user_id: int
    display_name: str
    first_name: str | None
    last_name: str | None
    bio: str | None
    avatar_url: str | None
    timezone: str | None
    locale: str | None
    created_at: datetime
    updated_at: datetime


class PublicUserProfileResponse(BaseModel):
    """Public-safe subset; no email or account metadata (timezone, locale, timestamps)."""

    model_config = ConfigDict(from_attributes=True)

    user_id: int
    display_name: str
    first_name: str | None
    last_name: str | None
    bio: str | None
    avatar_url: str | None
