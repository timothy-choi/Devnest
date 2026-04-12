"""Platform security helpers (internal auth, future mTLS hooks)."""

from .dependencies import require_internal_api_key
from .internal_auth import INTERNAL_API_SECRET_FIELD_NAMES, InternalApiScope, internal_api_expected_secrets

__all__ = [
    "INTERNAL_API_SECRET_FIELD_NAMES",
    "InternalApiScope",
    "internal_api_expected_secrets",
    "require_internal_api_key",
]
