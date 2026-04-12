"""Dependencies for notification routes."""

from app.libs.security.dependencies import require_internal_api_key

__all__ = ["require_internal_api_key"]
