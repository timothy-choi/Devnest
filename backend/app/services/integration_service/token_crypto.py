"""Symmetric encryption for provider OAuth tokens stored in the database.

Uses ``cryptography.fernet.Fernet`` (available as a transitive dependency via
``paramiko`` / ``bcrypt``). Keys are 32 random bytes, base64url-encoded.

If ``DEVNEST_TOKEN_ENCRYPTION_KEY`` is not configured, a key is derived from
``JWT_SECRET_KEY`` using SHA-256.  Production deployments **must** set a
dedicated, randomly generated ``DEVNEST_TOKEN_ENCRYPTION_KEY``.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

_logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    from app.libs.common.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    raw_key = getattr(settings, "devnest_token_encryption_key", "") or ""
    if not raw_key:
        # Derive from JWT secret — warn in production.
        raw_key = getattr(settings, "jwt_secret_key", "insecure-derive-key")
        _logger.warning(
            "token_crypto_using_derived_key",
            extra={
                "reason": "DEVNEST_TOKEN_ENCRYPTION_KEY not set; deriving from JWT secret. "
                "Set a dedicated encryption key in production."
            },
        )
    # Ensure exactly 32 bytes then base64url-encode for Fernet.
    key_bytes = hashlib.sha256(raw_key.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)


def encrypt_token(plain: str) -> str:
    """Encrypt a plain-text token and return the Fernet ciphertext as a UTF-8 string."""
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """Decrypt a Fernet ciphertext back to a plain-text token.

    Raises ``ValueError`` if the ciphertext is invalid or tampered with.
    """
    try:
        return _get_fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("token_decryption_failed") from exc


def invalidate_cache() -> None:
    """Clear the cached Fernet instance (useful when settings change in tests)."""
    _get_fernet.cache_clear()
