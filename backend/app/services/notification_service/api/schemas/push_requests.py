"""Push subscription registration."""

from typing import Literal

from pydantic import BaseModel, Field


class PushSubscriptionRegisterRequest(BaseModel):
    platform: Literal["WEB", "IOS", "ANDROID"]
    endpoint: str = Field(min_length=1, max_length=2048)
    p256dh_key: str | None = Field(default=None, max_length=512)
    auth_key: str | None = Field(default=None, max_length=256)
    device_token: str | None = Field(default=None, max_length=512)
    device_name: str | None = Field(default=None, max_length=255)
