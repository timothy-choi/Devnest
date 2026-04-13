"""Unit tests for provider token encryption / decryption."""

from __future__ import annotations

import pytest


def test_encrypt_decrypt_roundtrip(monkeypatch):
    """Encrypting then decrypting recovers the original token."""
    monkeypatch.setenv("DEVNEST_TOKEN_ENCRYPTION_KEY", "test-key-abc123")
    from app.services.integration_service.token_crypto import (
        decrypt_token,
        encrypt_token,
        invalidate_cache,
    )
    invalidate_cache()

    plain = "ghp_testtoken12345"
    ciphertext = encrypt_token(plain)
    assert ciphertext != plain
    assert decrypt_token(ciphertext) == plain
    invalidate_cache()


def test_different_plaintexts_produce_different_ciphertexts(monkeypatch):
    monkeypatch.setenv("DEVNEST_TOKEN_ENCRYPTION_KEY", "key-for-uniqueness")
    from app.services.integration_service.token_crypto import encrypt_token, invalidate_cache

    invalidate_cache()
    c1 = encrypt_token("token_a")
    c2 = encrypt_token("token_b")
    assert c1 != c2
    invalidate_cache()


def test_tampered_ciphertext_raises(monkeypatch):
    monkeypatch.setenv("DEVNEST_TOKEN_ENCRYPTION_KEY", "tamper-key")
    from app.services.integration_service.token_crypto import (
        decrypt_token,
        encrypt_token,
        invalidate_cache,
    )
    invalidate_cache()

    ciphertext = encrypt_token("good_token")
    tampered = ciphertext[:-4] + "XXXX"
    with pytest.raises(ValueError, match="token_decryption_failed"):
        decrypt_token(tampered)
    invalidate_cache()


def test_fallback_derivation_when_no_key_set(monkeypatch):
    """When DEVNEST_TOKEN_ENCRYPTION_KEY is empty, key derives from JWT secret without crashing."""
    monkeypatch.setenv("DEVNEST_TOKEN_ENCRYPTION_KEY", "")
    monkeypatch.setenv("JWT_SECRET_KEY", "jwt-secret-fallback")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()
    from app.services.integration_service.token_crypto import (
        decrypt_token,
        encrypt_token,
        invalidate_cache,
    )
    invalidate_cache()

    plain = "fallback_test"
    assert decrypt_token(encrypt_token(plain)) == plain
    invalidate_cache()
    get_settings.cache_clear()
