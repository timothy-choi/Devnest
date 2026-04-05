"""Preference update payloads."""

from pydantic import BaseModel, Field


class PreferenceUpsertItem(BaseModel):
    notification_type: str = Field(min_length=1, max_length=128)
    in_app_enabled: bool = True
    email_enabled: bool = True
    push_enabled: bool = True


class NotificationPreferencesPutRequest(BaseModel):
    """Replace or merge semantics decided in the service layer; body is a list of per-type rows."""

    preferences: list[PreferenceUpsertItem] = Field(min_length=1)
