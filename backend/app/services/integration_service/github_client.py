"""Lightweight GitHub REST API client using ``httpx``.

Only the operations needed by DevNest integrations are implemented:
- Fetching user profile (for token validation and display).
- Listing repositories visible to the token.
- Triggering ``repository_dispatch`` events (CI/CD).

All methods raise :class:`GitHubClientError` on unexpected responses.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

_logger = logging.getLogger(__name__)

_GITHUB_API_BASE = "https://api.github.com"
_TIMEOUT_SECONDS = 20.0


class GitHubClientError(Exception):
    """Raised when the GitHub API returns an unexpected response."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubClient:
    """Synchronous GitHub REST API client for a single user token."""

    def __init__(self, access_token: str) -> None:
        self._token = access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _get(self, path: str, **params: Any) -> Any:
        url = f"{_GITHUB_API_BASE}/{path.lstrip('/')}"
        resp = httpx.get(url, headers=self._headers(), params=params, timeout=_TIMEOUT_SECONDS)
        if not resp.is_success:
            raise GitHubClientError(
                f"GitHub API GET {path} failed ({resp.status_code}): {resp.text[:256]}",
                status_code=resp.status_code,
            )
        return resp.json()

    def _post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        url = f"{_GITHUB_API_BASE}/{path.lstrip('/')}"
        resp = httpx.post(
            url, headers=self._headers(), json=body, timeout=_TIMEOUT_SECONDS
        )
        return resp.status_code, (resp.json() if resp.content else {})

    # ── Public methods ────────────────────────────────────────────────────────

    def get_user(self) -> dict[str, Any]:
        """Return the authenticated user's profile."""
        return self._get("/user")

    def list_repos(self, *, per_page: int = 30, page: int = 1) -> list[dict[str, Any]]:
        """Return repositories visible to the token."""
        return self._get(
            "/user/repos",
            visibility="all",
            sort="updated",
            per_page=per_page,
            page=page,
        )

    def trigger_repository_dispatch(
        self,
        owner: str,
        repo: str,
        *,
        event_type: str = "devnest_trigger",
        client_payload: dict[str, Any] | None = None,
    ) -> bool:
        """Send a ``repository_dispatch`` event to trigger a GitHub Actions workflow.

        Returns True when GitHub accepted the event (HTTP 204).
        Raises ``GitHubClientError`` on auth or permission failures.
        """
        status, body = self._post(
            f"/repos/{owner}/{repo}/dispatches",
            {"event_type": event_type, "client_payload": client_payload or {}},
        )
        if status == 204:
            return True
        if status in (401, 403):
            raise GitHubClientError(
                f"GitHub dispatch forbidden — check token scopes (repo required): {body}",
                status_code=status,
            )
        raise GitHubClientError(
            f"GitHub dispatch unexpected status {status}: {body}",
            status_code=status,
        )
