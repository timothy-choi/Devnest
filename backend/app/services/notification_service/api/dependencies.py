"""Dependencies for notification routes (internal API key)."""

from fastapi import Header, HTTPException, status

from app.libs.common.config import get_settings


def require_internal_api_key(x_internal_api_key: str | None = Header(default=None, alias="X-Internal-API-Key")) -> None:
    expected = get_settings().internal_api_key
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal API is not configured",
        )
    if not x_internal_api_key or x_internal_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing internal API key",
        )
