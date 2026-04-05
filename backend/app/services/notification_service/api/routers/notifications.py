"""User-facing notification, preference, and push subscription routes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlmodel import Session

from app.libs.db.database import get_db
from app.services.auth_service.api.dependencies import get_current_user
from app.services.auth_service.models import UserAuth
from app.services.notification_service.models import Notification, NotificationRecipient
from app.services.notification_service.services import (
    notification_service,
    preference_service,
    push_subscription_service,
)
from app.services.notification_service.services.exceptions import (
    NotificationNotFoundError,
    PushSubscriptionNotFoundError,
)

from ..schemas import (
    NotificationDetailResponse,
    NotificationItemResponse,
    NotificationListResponse,
    NotificationPreferenceResponse,
    NotificationPreferencesListResponse,
    NotificationPreferencesPutRequest,
    NotificationReadBulkRequest,
    PushSubscriptionRegisterRequest,
    PushSubscriptionResponse,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _to_item(n: Notification, r: NotificationRecipient) -> NotificationItemResponse:
    assert n.notification_id is not None
    assert r.notification_recipient_id is not None
    return NotificationItemResponse(
        notification_id=n.notification_id,
        notification_recipient_id=r.notification_recipient_id,
        type=n.type,
        title=n.title,
        body=n.body,
        payload=n.payload_json,
        priority=n.priority,
        source_service=n.source_service,
        source_event_id=n.source_event_id,
        recipient_status=r.status,
        read_at=r.read_at,
        dismissed_at=r.dismissed_at,
        created_at=n.created_at,
    )


def _to_detail(n: Notification, r: NotificationRecipient) -> NotificationDetailResponse:
    assert n.notification_id is not None
    assert r.notification_recipient_id is not None
    return NotificationDetailResponse(
        notification_id=n.notification_id,
        notification_recipient_id=r.notification_recipient_id,
        type=n.type,
        title=n.title,
        body=n.body,
        payload=n.payload_json,
        priority=n.priority,
        status=n.status,
        source_service=n.source_service,
        source_event_id=n.source_event_id,
        recipient_status=r.status,
        read_at=r.read_at,
        dismissed_at=r.dismissed_at,
        created_at=n.created_at,
    )


@router.get(
    "",
    response_model=NotificationListResponse,
    status_code=status.HTTP_200_OK,
    summary="List notifications for the current user",
)
def list_notifications(
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
    filter_mode: Literal["all", "unread", "read"] = Query(default="all"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> NotificationListResponse:
    assert current.user_auth_id is not None
    rows, total = notification_service.list_notifications_for_user(
        session,
        current.user_auth_id,
        filter_mode=filter_mode,
        limit=limit,
        offset=offset,
    )
    return NotificationListResponse(items=[_to_item(n, r) for n, r in rows], total=total)


@router.put(
    "/read-bulk",
    response_model=list[NotificationItemResponse],
    status_code=status.HTTP_200_OK,
    summary="Mark multiple notifications as read",
)
def mark_notifications_read_bulk(
    body: NotificationReadBulkRequest,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> list[NotificationItemResponse]:
    assert current.user_auth_id is not None
    updated = notification_service.mark_read_bulk(session, current.user_auth_id, body.notification_ids)
    out: list[NotificationItemResponse] = []
    for rec in updated:
        n = notification_service.get_notification_for_user(session, current.user_auth_id, rec.notification_id)[0]
        out.append(_to_item(n, rec))
    return out


@router.get(
    "/preferences",
    response_model=NotificationPreferencesListResponse,
    status_code=status.HTTP_200_OK,
    summary="List notification channel preferences",
)
def get_preferences(
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> NotificationPreferencesListResponse:
    assert current.user_auth_id is not None
    prefs = preference_service.get_preferences(session, current.user_auth_id)
    return NotificationPreferencesListResponse(
        preferences=[NotificationPreferenceResponse.model_validate(p) for p in prefs],
    )


@router.put(
    "/preferences",
    response_model=NotificationPreferencesListResponse,
    status_code=status.HTTP_200_OK,
    summary="Upsert notification channel preferences (per notification type)",
)
def put_preferences(
    body: NotificationPreferencesPutRequest,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> NotificationPreferencesListResponse:
    assert current.user_auth_id is not None
    for item in body.preferences:
        preference_service.upsert_preference(
            session,
            user_id=current.user_auth_id,
            notification_type=item.notification_type,
            in_app_enabled=item.in_app_enabled,
            email_enabled=item.email_enabled,
            push_enabled=item.push_enabled,
        )
    prefs = preference_service.get_preferences(session, current.user_auth_id)
    return NotificationPreferencesListResponse(
        preferences=[NotificationPreferenceResponse.model_validate(p) for p in prefs],
    )


@router.get(
    "/push/subscriptions",
    response_model=list[PushSubscriptionResponse],
    status_code=status.HTTP_200_OK,
    summary="List active push subscriptions",
)
def list_push_subscriptions(
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> list[PushSubscriptionResponse]:
    assert current.user_auth_id is not None
    rows = push_subscription_service.list_subscriptions(session, current.user_auth_id)
    return [PushSubscriptionResponse.model_validate(s) for s in rows]


@router.post(
    "/push/subscriptions",
    response_model=PushSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a push subscription",
)
def register_push_subscription(
    body: PushSubscriptionRegisterRequest,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> PushSubscriptionResponse:
    assert current.user_auth_id is not None
    row = push_subscription_service.register_subscription(
        session,
        user_id=current.user_auth_id,
        platform=body.platform,
        endpoint=body.endpoint,
        p256dh_key=body.p256dh_key,
        auth_key=body.auth_key,
        device_token=body.device_token,
        device_name=body.device_name,
    )
    return PushSubscriptionResponse.model_validate(row)


@router.delete(
    "/push/subscriptions/{push_subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Revoke a push subscription",
)
def revoke_push_subscription(
    push_subscription_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> Response:
    assert current.user_auth_id is not None
    try:
        push_subscription_service.revoke_subscription(session, current.user_auth_id, push_subscription_id)
    except PushSubscriptionNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Push subscription not found") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{notification_id}",
    response_model=NotificationDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get one notification for the current user",
)
def get_notification(
    notification_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> NotificationDetailResponse:
    assert current.user_auth_id is not None
    try:
        n, r = notification_service.get_notification_for_user(session, current.user_auth_id, notification_id)
    except NotificationNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found") from None
    return _to_detail(n, r)


@router.put(
    "/{notification_id}/read",
    response_model=NotificationItemResponse,
    status_code=status.HTTP_200_OK,
    summary="Mark a notification as read",
)
def mark_notification_read(
    notification_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> NotificationItemResponse:
    assert current.user_auth_id is not None
    try:
        rec = notification_service.mark_read(session, current.user_auth_id, notification_id)
    except NotificationNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found") from None
    n = notification_service.get_notification_for_user(session, current.user_auth_id, notification_id)[0]
    return _to_item(n, rec)


@router.put(
    "/{notification_id}/dismiss",
    response_model=NotificationItemResponse,
    status_code=status.HTTP_200_OK,
    summary="Dismiss a notification",
)
def dismiss_notification(
    notification_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> NotificationItemResponse:
    assert current.user_auth_id is not None
    try:
        rec = notification_service.dismiss_notification(session, current.user_auth_id, notification_id)
    except NotificationNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found") from None
    n = notification_service.get_notification_for_user(session, current.user_auth_id, notification_id)[0]
    return _to_item(n, rec)
