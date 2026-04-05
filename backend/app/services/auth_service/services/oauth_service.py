"""OAuth start URL and callback: exchange code, link or create user, issue tokens."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.services.auth_service.models import OAuth, Token, UserAuth
from app.services.auth_service.services.auth_token import create_access_token
from app.services.auth_service.services.login_service import LoginTokens
from app.services.auth_service.services.oauth_client import (
    OAuthProviderError,
    build_github_authorization_url,
    build_google_authorization_url,
    exchange_github_code,
    exchange_google_code,
    fetch_github_profile,
    fetch_google_profile,
)
from app.services.auth_service.services.oauth_state import create_oauth_state, verify_oauth_state
from app.services.auth_service.services.refresh_token_crypto import hash_refresh_token, new_refresh_token_value


ALLOWED_OAUTH_PROVIDERS = frozenset({"github", "google"})


class UnsupportedOAuthProviderError(Exception):
    """Path did not match a supported provider."""


def normalize_oauth_provider(provider: str) -> str:
    p = provider.lower().strip()
    if p not in ALLOWED_OAUTH_PROVIDERS:
        raise UnsupportedOAuthProviderError(provider)
    return p


def oauth_placeholder_password_hash() -> str:
    return bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt()).decode()


def start_oauth_authorization_url(*, provider: str) -> str:
    p = normalize_oauth_provider(provider)
    state = create_oauth_state(provider=p)
    if p == "github":
        return build_github_authorization_url(state=state)
    return build_google_authorization_url(state=state)


def _unique_username(session: Session, base: str) -> str:
    root = (base or "user")[:255]
    candidate = root
    n = 0
    while True:
        existing = session.exec(select(UserAuth).where(UserAuth.username == candidate)).first()
        if existing is None:
            return candidate
        n += 1
        suffix = f"_{n}"
        candidate = f"{root[: 255 - len(suffix)]}{suffix}"


def _issue_tokens(session: Session, user: UserAuth) -> LoginTokens:
    assert user.user_auth_id is not None
    refresh_plain = new_refresh_token_value()
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=settings.refresh_token_expire_days)
    token_row = Token(
        user_id=user.user_auth_id,
        token_hash=hash_refresh_token(refresh_plain),
        expires_at=expires_at,
        revoked=False,
    )
    session.add(token_row)
    session.commit()
    session.refresh(token_row)
    access = create_access_token(user_id=user.user_auth_id)
    return LoginTokens(access_token=access, refresh_token=refresh_plain)


def complete_oauth(
    session: Session,
    *,
    provider: str,
    code: str,
    state: str,
) -> LoginTokens:
    p = normalize_oauth_provider(provider)
    verify_oauth_state(state, expected_provider=p)
    if p == "github":
        access = exchange_github_code(code=code)
        profile = fetch_github_profile(access_token=access)
    else:
        access = exchange_google_code(code=code)
        profile = fetch_google_profile(access_token=access)

    oauth_row = session.exec(
        select(OAuth).where(OAuth.oauth_provider == p, OAuth.provider_user_id == profile.provider_user_id)
    ).first()
    if oauth_row is not None:
        user = session.get(UserAuth, oauth_row.user_id)
        if user is None:
            raise OAuthProviderError("OAuth row references missing user")
        return _issue_tokens(session, user)

    existing = session.exec(select(UserAuth).where(UserAuth.email == profile.email)).first()
    if existing is not None:
        assert existing.user_auth_id is not None
        session.add(
            OAuth(
                user_id=existing.user_auth_id,
                oauth_provider=p,
                provider_user_id=profile.provider_user_id,
            )
        )
        session.commit()
        return _issue_tokens(session, existing)

    base_name = profile.username or f"{p}_{profile.provider_user_id}"
    username = _unique_username(session, base_name)
    user = UserAuth(
        username=username,
        email=profile.email,
        password_hash=oauth_placeholder_password_hash(),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.user_auth_id is not None
    session.add(
        OAuth(
            user_id=user.user_auth_id,
            oauth_provider=p,
            provider_user_id=profile.provider_user_id,
        )
    )
    session.commit()
    return _issue_tokens(session, user)
