"""Request bodies for user/profile APIs."""

from pydantic import BaseModel, Field


class UpdateMyProfileRequest(BaseModel):
    """PATCH /users/me — omitted fields are left unchanged."""

    display_name: str | None = Field(default=None, max_length=255)
    first_name: str | None = Field(default=None, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)
    bio: str | None = Field(default=None, max_length=8192)
    avatar_url: str | None = Field(default=None, max_length=2048)
    timezone: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, max_length=32)
