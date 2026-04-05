"""Push subscription API responses."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PushSubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    push_subscription_id: int
    platform: str
    endpoint: str
    device_name: str | None
    last_seen_at: datetime
    revoked: bool
    created_at: datetime
    updated_at: datetime
