"""Platform security helpers (internal auth, future mTLS hooks)."""

from .dependencies import require_internal_api_key
from .internal_auth import InternalApiScope, internal_api_expected_secrets

__all__ = ["InternalApiScope", "internal_api_expected_secrets", "require_internal_api_key"]
