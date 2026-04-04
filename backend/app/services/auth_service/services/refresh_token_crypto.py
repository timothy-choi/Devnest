"""Opaque refresh token generation and hashing for persistence."""

import hashlib
import secrets


def new_refresh_token_value() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
