"""Internal service-to-service notification endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.libs.db.database import get_db
from app.libs.observability.log_events import LogEvent, log_event
from app.libs.security.internal_auth import InternalApiScope
from app.services.notification_service.services import delivery_service, notification_service
from app.services.notification_service.services.exceptions import (
    DeliveryNotFoundError,
    InvalidDeliveryStateError,
)

from app.libs.security.dependencies import require_internal_api_key

_logger = logging.getLogger(__name__)
from ..schemas import (
    DeliveryRetryResponse,
    InternalCreateNotificationRequest,
    InternalCreateNotificationResponse,
)

router = APIRouter(
    prefix="/internal/notifications",
    tags=["internal-notifications"],
    dependencies=[Depends(require_internal_api_key(InternalApiScope.NOTIFICATIONS))],
)


@router.post(
    "",
    response_model=InternalCreateNotificationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a notification and dispatch to recipients (internal)",
)
def internal_create_notification(
    body: InternalCreateNotificationRequest,
    session: Session = Depends(get_db),
) -> InternalCreateNotificationResponse:
    log_event(
        _logger,
        LogEvent.AUDIT_INTERNAL_NOTIFICATIONS_CREATE,
        notification_type=body.type,
        recipient_count=len(body.recipient_user_ids),
    )
    priority = notification_service.validate_priority(body.priority)
    notif = notification_service.create_notification_event(
        session,
        type=body.type,
        title=body.title,
        body=body.body,
        payload_json=body.payload,
        recipient_user_ids=body.recipient_user_ids,
        priority=priority,
        source_service=body.source_service,
        source_event_id=body.source_event_id,
    )
    assert notif.notification_id is not None
    return InternalCreateNotificationResponse(notification_id=notif.notification_id, status=notif.status)


@router.post(
    "/deliveries/{delivery_id}/retry",
    response_model=DeliveryRetryResponse,
    status_code=status.HTTP_200_OK,
    summary="Retry a failed delivery (internal)",
)
def internal_retry_delivery(delivery_id: int, session: Session = Depends(get_db)) -> DeliveryRetryResponse:
    log_event(_logger, LogEvent.AUDIT_INTERNAL_NOTIFICATIONS_RETRY_DELIVERY, delivery_id=delivery_id)
    try:
        d = delivery_service.retry_delivery(session, delivery_id)
    except DeliveryNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found") from None
    except InvalidDeliveryStateError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Delivery is not in a failed state",
        ) from None
    assert d.delivery_id is not None
    return DeliveryRetryResponse(
        delivery_id=d.delivery_id,
        status=d.status,
        attempt_count=d.attempt_count,
        last_error_message=d.last_error_message,
    )
