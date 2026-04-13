"""Unit tests for CI/CD trigger logic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_trigger_calls_github_dispatch(monkeypatch):
    """Triggering CI calls GitHubClient.trigger_repository_dispatch."""
    import httpx
    from app.services.integration_service.github_client import GitHubClient

    called_with: dict = {}

    original_post = httpx.post

    def mock_post(url, **kwargs):
        called_with["url"] = url
        called_with["body"] = kwargs.get("json", {})
        return httpx.Response(204, content=b"")

    monkeypatch.setattr(httpx, "post", mock_post)
    client = GitHubClient("token")
    result = client.trigger_repository_dispatch(
        "owner", "my-repo",
        event_type="devnest_trigger",
        client_payload={"workspace_id": 42},
    )
    assert result is True
    assert "repos/owner/my-repo/dispatches" in called_with["url"]
    assert called_with["body"]["event_type"] == "devnest_trigger"
    assert called_with["body"]["client_payload"]["workspace_id"] == 42


def test_trigger_with_insufficient_scope_raises(monkeypatch):
    """403 from GitHub raises GitHubClientError."""
    import httpx
    from app.services.integration_service.github_client import GitHubClient, GitHubClientError

    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(403, json={"message": "scope required"}))
    client = GitHubClient("token_no_repo_scope")
    with pytest.raises(GitHubClientError) as exc:
        client.trigger_repository_dispatch("o", "r")
    assert exc.value.status_code == 403
    assert "scope" in str(exc.value).lower()
