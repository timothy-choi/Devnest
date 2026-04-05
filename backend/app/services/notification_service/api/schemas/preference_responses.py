"""Preference API responses."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NotificationPreferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    preference_id: int
    notification_type: str
    in_app_enabled: bool
    email_enabled: bool
    push_enabled: bool
    created_at: datetime
    updated_at: datetime


class NotificationPreferencesListResponse(BaseModel):
    preferences: list[NotificationPreferenceResponse]
