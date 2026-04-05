"""Register and revoke push endpoints for a user."""

from __future__ import annotations

from sqlmodel import Session

from app.services.notification_service.models import PushSubscription
from app.services.notification_service.repositories import push_subscription_repo
from app.services.notification_service.services.exceptions import PushSubscriptionNotFoundError


def register_subscription(
    session: Session,
    *,
    user_id: int,
    platform: str,
    endpoint: str,
    p256dh_key: str | None,
    auth_key: str | None,
    device_token: str | None,
    device_name: str | None,
) -> PushSubscription:
    return push_subscription_repo.create_subscription(
        session,
        user_id=user_id,
        platform=platform,
        endpoint=endpoint,
        p256dh_key=p256dh_key,
        auth_key=auth_key,
        device_token=device_token,
        device_name=device_name,
    )


def list_subscriptions(session: Session, user_id: int) -> list[PushSubscription]:
    return push_subscription_repo.list_subscriptions_for_user(session, user_id, include_revoked=False)


def revoke_subscription(session: Session, user_id: int, push_subscription_id: int) -> PushSubscription:
    row = push_subscription_repo.get_subscription_for_user(session, push_subscription_id, user_id)
    if row is None:
        raise PushSubscriptionNotFoundError
    return push_subscription_repo.revoke_subscription(session, row)
