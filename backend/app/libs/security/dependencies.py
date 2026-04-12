"""FastAPI dependencies for platform security (internal API credentials)."""

from __future__ import annotations

import logging

from fastapi import Header, HTTPException, status

from app.libs.common.config import get_settings
from app.libs.observability.correlation import get_correlation_id
from app.libs.observability.log_events import LogEvent, log_event
from app.libs.observability.metrics import record_internal_auth_failure
from app.libs.security.internal_auth import InternalApiScope, internal_api_expected_secrets, internal_api_key_is_valid

_logger = logging.getLogger(__name__)


def require_internal_api_key(scope: InternalApiScope):
    """Validate ``X-Internal-API-Key`` for the given internal surface (see :class:`InternalApiScope`)."""

    def _dep(x_internal_api_key: str | None = Header(default=None, alias="X-Internal-API-Key")) -> None:
        settings = get_settings()
        cid = (get_correlation_id() or "").strip() or None
        if not internal_api_expected_secrets(settings, scope):
            log_event(
                _logger,
                LogEvent.SECURITY_INTERNAL_NOT_CONFIGURED,
                level=logging.WARNING,
                correlation_id=cid,
                internal_scope=scope.value,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Internal API is not configured",
            )
        if not internal_api_key_is_valid(x_internal_api_key, settings, scope):
            record_internal_auth_failure(scope=scope.value)
            log_event(
                _logger,
                LogEvent.SECURITY_INTERNAL_AUTH_FAILED,
                level=logging.WARNING,
                correlation_id=cid,
                internal_scope=scope.value,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing internal API key",
            )

    return _dep
