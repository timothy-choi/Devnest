"""OAuth provider connect/disconnect routes (Task 1: GitHub + Google OAuth for repo access).

Separate from the sign-in OAuth flow (``/auth/oauth/{provider}``).  These routes let an
already-authenticated DevNest user **connect** a provider account with extended scopes
(e.g. GitHub ``repo``) and store the resulting access token for workspace operations.

Routes
------
GET  /auth/provider-tokens                   — list the current user's connected providers
POST /auth/provider-tokens/{provider}/connect — start OAuth flow with extended scopes
GET  /auth/provider-tokens/{provider}/callback — exchange code, store token, redirect/return
DELETE /auth/provider-tokens/{token_id}       — revoke / disconnect a provider token
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from app.libs.db.database import get_db
from app.services.auth_service.api.dependencies import get_current_user
from app.services.auth_service.models import UserAuth
from app.services.auth_service.services.oauth_client import (
    OAuthProviderError,
    build_github_authorization_url,
    exchange_github_code,
    fetch_github_profile,
    normalize_oauth_public_base,
    oauth_redirect_uri,
)
from app.services.auth_service.services.oauth_state import (
    OAuthStateError,
    create_oauth_state,
    verify_oauth_state,
)
from app.services.integration_service.api.schemas import (
    ProviderConnectStartResponse,
    ProviderTokenResponse,
)
from app.services.integration_service.models import UserProviderToken
from app.services.integration_service.token_crypto import decrypt_token, encrypt_token

_logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/provider-tokens", tags=["provider-tokens"])

_GITHUB_REPO_SCOPES = "read:user user:email repo"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_token_row(session: Session, user_id: int, provider: str) -> UserProviderToken | None:
    return session.exec(
        select(UserProviderToken).where(
            UserProviderToken.user_id == user_id,
            UserProviderToken.provider == provider,
        )
    ).first()


def _token_to_response(row: UserProviderToken) -> ProviderTokenResponse:
    return ProviderTokenResponse(
        token_id=row.token_id,
        provider=row.provider,
        provider_username=row.provider_username,
        scopes=row.scopes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _build_github_connect_url(*, state: str) -> str:
    """Build GitHub authorize URL requesting ``repo`` scope."""
    from app.libs.common.config import get_settings  # noqa: PLC0415
    s = get_settings()
    if not s.oauth_github_client_id:
        raise OAuthProviderError("GitHub OAuth is not configured")
    # Use the existing callback path for sign-in but with extended scopes.
    # We differentiate sign-in vs connect via the OAuth state nonce.
    redirect_uri = _connect_redirect_uri("github")
    q = urlencode(
        {
            "client_id": s.oauth_github_client_id,
            "redirect_uri": redirect_uri,
            "scope": _GITHUB_REPO_SCOPES,
            "state": state,
        }
    )
    return f"https://github.com/login/oauth/authorize?{q}"


def _connect_redirect_uri(provider: str) -> str:
    """Redirect URI for the /connect/callback endpoint."""
    from app.libs.common.config import get_settings  # noqa: PLC0415
    s = get_settings()
    if provider == "github":
        raw = s.github_oauth_public_base_url
    else:
        raw = s.gcloud_oauth_public_base_url
    base = normalize_oauth_public_base(raw)
    if not base:
        raise OAuthProviderError(f"{provider} OAuth public base URL is not configured")
    return f"{base}/auth/provider-tokens/{provider}/callback"


def _exchange_github_connect_code(*, code: str) -> tuple[str, str]:
    """Exchange code for token using the connect callback redirect URI.

    Returns (access_token, scopes).
    """
    from app.libs.common.config import get_settings  # noqa: PLC0415
    s = get_settings()
    redirect_uri = _connect_redirect_uri("github")
    r = httpx.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": s.oauth_github_client_id,
            "client_secret": s.oauth_github_client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=30.0,
    )
    if r.status_code >= 400:
        raise OAuthProviderError(f"GitHub token exchange failed: {r.status_code}")
    data = r.json()
    err = data.get("error")
    if err:
        raise OAuthProviderError(f"GitHub token error: {err}")
    token = data.get("access_token", "")
    if not token:
        raise OAuthProviderError("GitHub token response missing access_token")
    scopes = data.get("scope", "")
    return str(token), str(scopes)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=list[ProviderTokenResponse],
    summary="List connected OAuth providers for the current user",
)
def list_provider_tokens(
    current: UserAuth = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[ProviderTokenResponse]:
    assert current.user_auth_id is not None
    rows = session.exec(
        select(UserProviderToken).where(UserProviderToken.user_id == current.user_auth_id)
    ).all()
    return [_token_to_response(r) for r in rows]


@router.post(
    "/{provider}/connect",
    response_model=ProviderConnectStartResponse,
    status_code=status.HTTP_200_OK,
    summary="Start OAuth provider connect flow with extended scopes",
)
def start_provider_connect(
    provider: str,
    current: UserAuth = Depends(get_current_user),
) -> ProviderConnectStartResponse:
    if provider not in ("github",):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider for repo connect: {provider!r}. Currently supported: github",
        )
    try:
        state = create_oauth_state(provider=provider)
        if provider == "github":
            url = _build_github_connect_url(state=state)
        else:
            raise HTTPException(400, detail=f"Provider {provider} not yet supported")
    except OAuthProviderError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ProviderConnectStartResponse(authorization_url=url, provider=provider)


@router.get(
    "/{provider}/callback",
    summary="OAuth provider connect callback — exchange code and store token",
)
def provider_connect_callback(
    provider: str,
    code: str = Query(...),
    state: str = Query(...),
    current: UserAuth = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> ProviderTokenResponse:
    if provider not in ("github",):
        raise HTTPException(400, detail=f"Unsupported provider: {provider}")

    try:
        verify_oauth_state(state, expected_provider=provider)
    except OAuthStateError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        if provider == "github":
            access_token, scopes = _exchange_github_connect_code(code=code)
            profile = fetch_github_profile(access_token=access_token)
        else:
            raise HTTPException(400, detail=f"Provider {provider} not yet implemented")
    except OAuthProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    assert current.user_auth_id is not None
    now = datetime.now(timezone.utc)
    encrypted_token = encrypt_token(access_token)

    existing = _get_token_row(session, current.user_auth_id, provider)
    if existing is not None:
        existing.access_token_encrypted = encrypted_token
        existing.scopes = scopes
        existing.provider_user_id = profile.provider_user_id
        existing.provider_username = profile.username
        existing.updated_at = now
        session.add(existing)
        session.commit()
        session.refresh(existing)
        _logger.info("provider_token_updated", extra={"user_id": current.user_auth_id, "provider": provider})
        return _token_to_response(existing)

    row = UserProviderToken(
        user_id=current.user_auth_id,
        provider=provider,
        access_token_encrypted=encrypted_token,
        scopes=scopes,
        provider_user_id=profile.provider_user_id,
        provider_username=profile.username,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _token_to_response(row)


@router.delete(
    "/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Revoke / disconnect a stored provider token",
)
def delete_provider_token(
    token_id: int,
    current: UserAuth = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    assert current.user_auth_id is not None
    row = session.get(UserProviderToken, token_id)
    if row is None or row.user_id != current.user_auth_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider token not found")
    session.delete(row)
    session.commit()


# ── Helper used by other integration routes ───────────────────────────────────

def resolve_provider_token(
    session: Session,
    user_id: int,
    provider: str,
) -> str | None:
    """Return the plaintext provider access token for ``user_id`` / ``provider``, or None."""
    row = _get_token_row(session, user_id, provider)
    if row is None:
        return None
    try:
        return decrypt_token(row.access_token_encrypted)
    except ValueError:
        _logger.warning("provider_token_decrypt_failed", extra={"user_id": user_id, "provider": provider})
        return None
