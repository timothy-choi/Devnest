"""Persistence for ``PushSubscription`` rows."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from app.services.notification_service.models import PushSubscription


def create_subscription(
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
    now = datetime.now(timezone.utc)
    row = PushSubscription(
        user_id=user_id,
        platform=platform,
        endpoint=endpoint,
        p256dh_key=p256dh_key,
        auth_key=auth_key,
        device_token=device_token,
        device_name=device_name,
        last_seen_at=now,
        revoked=False,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_subscription_by_id(session: Session, push_subscription_id: int) -> PushSubscription | None:
    return session.get(PushSubscription, push_subscription_id)


def get_subscription_for_user(
    session: Session,
    push_subscription_id: int,
    user_id: int,
) -> PushSubscription | None:
    return session.exec(
        select(PushSubscription).where(
            PushSubscription.push_subscription_id == push_subscription_id,
            PushSubscription.user_id == user_id,
        )
    ).first()


def list_subscriptions_for_user(
    session: Session,
    user_id: int,
    *,
    include_revoked: bool = False,
) -> list[PushSubscription]:
    stmt = select(PushSubscription).where(PushSubscription.user_id == user_id)
    if not include_revoked:
        stmt = stmt.where(PushSubscription.revoked == False)  # noqa: E712
    return list(session.exec(stmt).all())


def revoke_subscription(session: Session, row: PushSubscription) -> PushSubscription:
    row.revoked = True
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def touch_last_seen(session: Session, row: PushSubscription) -> PushSubscription:
    row.last_seen_at = datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
