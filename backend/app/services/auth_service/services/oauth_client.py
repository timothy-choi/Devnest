"""OAuth provider HTTP: authorization URLs, token exchange, user profile fetch."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.libs.common.config import get_settings


class OAuthProviderError(Exception):
    """Provider returned an error or unexpected response."""


@dataclass(frozen=True)
class OAuthProfile:
    provider_user_id: str
    email: str
    username: str


def oauth_callback_path(provider: str) -> str:
    return f"/auth/oauth/{provider}/callback"


def normalize_oauth_public_base(raw: str) -> str:
    """Allow env like `localhost:3003` or full `http://localhost:3003`."""
    s = raw.strip().rstrip("/")
    if not s:
        return ""
    if not s.startswith(("http://", "https://")):
        return f"http://{s}"
    return s


def oauth_redirect_uri(provider: str) -> str:
    s = get_settings()
    if provider == "github":
        raw = s.github_oauth_public_base_url
    elif provider == "google":
        raw = s.gcloud_oauth_public_base_url
    else:
        raise OAuthProviderError(f"Unknown OAuth provider: {provider}")
    base = normalize_oauth_public_base(raw)
    if not base:
        raise OAuthProviderError(
            f"{provider} OAuth is not configured (set GITHUB_OAUTH_PUBLIC_BASE_URL or GCLOUD_OAUTH_PUBLIC_BASE_URL)"
        )
    return f"{base}{oauth_callback_path(provider)}"


def build_github_authorization_url(*, state: str) -> str:
    s = get_settings()
    if not s.oauth_github_client_id:
        raise OAuthProviderError("GitHub OAuth is not configured")
    redirect_uri = oauth_redirect_uri("github")
    q = urlencode(
        {
            "client_id": s.oauth_github_client_id,
            "redirect_uri": redirect_uri,
            "scope": "read:user user:email",
            "state": state,
        }
    )
    return f"https://github.com/login/oauth/authorize?{q}"


def build_google_authorization_url(*, state: str) -> str:
    s = get_settings()
    if not s.oauth_google_client_id:
        raise OAuthProviderError("Google OAuth is not configured")
    redirect_uri = oauth_redirect_uri("google")
    q = urlencode(
        {
            "client_id": s.oauth_google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "include_granted_scopes": "true",
        }
    )
    return f"https://accounts.google.com/o/oauth2/v2/auth?{q}"


def exchange_github_code(*, code: str) -> str:
    s = get_settings()
    redirect_uri = oauth_redirect_uri("github")
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
    token = data.get("access_token")
    if not token:
        raise OAuthProviderError("GitHub token response missing access_token")
    return str(token)


def exchange_google_code(*, code: str) -> str:
    s = get_settings()
    redirect_uri = oauth_redirect_uri("google")
    r = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": s.oauth_google_client_id,
            "client_secret": s.oauth_google_client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30.0,
    )
    if r.status_code >= 400:
        raise OAuthProviderError(f"Google token exchange failed: {r.status_code}")
    data = r.json()
    if "access_token" not in data:
        raise OAuthProviderError("Google token response missing access_token")
    return str(data["access_token"])


def fetch_github_profile(*, access_token: str) -> OAuthProfile:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
    }
    ur = httpx.get("https://api.github.com/user", headers=headers, timeout=30.0)
    if ur.status_code >= 400:
        raise OAuthProviderError(f"GitHub user failed: {ur.status_code}")
    u = ur.json()
    uid = str(u.get("id", ""))
    if not uid:
        raise OAuthProviderError("GitHub user missing id")
    login = (u.get("login") or "").strip() or f"gh_{uid}"
    email = (u.get("email") or "").strip()
    if not email:
        er = httpx.get("https://api.github.com/user/emails", headers=headers, timeout=30.0)
        if er.status_code >= 400:
            raise OAuthProviderError("GitHub emails failed")
        for row in er.json():
            if row.get("primary") and row.get("email"):
                email = str(row["email"]).strip()
                break
        if not email:
            for row in er.json():
                if row.get("email"):
                    email = str(row["email"]).strip()
                    break
    if not email:
        raise OAuthProviderError("GitHub user has no email (grant user:email)")
    login = _safe_username(login)
    return OAuthProfile(provider_user_id=uid, email=email.lower(), username=login)


def fetch_google_profile(*, access_token: str) -> OAuthProfile:
    r = httpx.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30.0,
    )
    if r.status_code >= 400:
        raise OAuthProviderError(f"Google userinfo failed: {r.status_code}")
    u = r.json()
    uid = str(u.get("id") or u.get("sub") or "")
    if not uid:
        raise OAuthProviderError("Google user missing id")
    email = (u.get("email") or "").strip()
    if not email:
        raise OAuthProviderError("Google user missing email")
    name = (u.get("name") or email.split("@")[0]).strip()
    name = _safe_username(name) or f"g_{uid}"
    return OAuthProfile(provider_user_id=uid, email=email.lower(), username=name)


_slug_re = re.compile(r"[^a-zA-Z0-9._-]+")


def _safe_username(raw: str) -> str:
    s = _slug_re.sub("_", raw).strip("._-")
    return s[:255] if s else ""
