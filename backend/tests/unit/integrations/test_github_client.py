"""Unit tests for the GitHub REST API client."""

from __future__ import annotations

import pytest
import httpx


def _mock_transport(status_code: int, json_body: object, content: bytes | None = None):
    """Build an httpx mock transport that returns the given status and body."""
    import json as _json

    body = content if content is not None else _json.dumps(json_body).encode()

    class _Transport(httpx.BaseTransport):
        def handle_request(self, request):
            return httpx.Response(status_code, content=body)

    return _Transport()


def test_get_user_success(monkeypatch):
    from app.services.integration_service.github_client import GitHubClient

    profile = {"id": 1, "login": "alice", "email": "alice@example.com"}

    def mock_get(url, **kwargs):
        return httpx.Response(200, json=profile)

    monkeypatch.setattr(httpx, "get", mock_get)
    client = GitHubClient("test-token")
    result = client.get_user()
    assert result["login"] == "alice"


def test_get_user_failure_raises(monkeypatch):
    from app.services.integration_service.github_client import GitHubClient, GitHubClientError

    def mock_get(url, **kwargs):
        return httpx.Response(401, json={"message": "Bad credentials"})

    monkeypatch.setattr(httpx, "get", mock_get)
    client = GitHubClient("bad-token")
    with pytest.raises(GitHubClientError) as exc_info:
        client.get_user()
    assert exc_info.value.status_code == 401


def test_trigger_repository_dispatch_success(monkeypatch):
    from app.services.integration_service.github_client import GitHubClient

    def mock_post(url, **kwargs):
        return httpx.Response(204, content=b"")

    monkeypatch.setattr(httpx, "post", mock_post)
    client = GitHubClient("token")
    result = client.trigger_repository_dispatch("owner", "repo", event_type="devnest_trigger")
    assert result is True


def test_trigger_dispatch_403_raises(monkeypatch):
    from app.services.integration_service.github_client import GitHubClient, GitHubClientError

    def mock_post(url, **kwargs):
        return httpx.Response(403, json={"message": "Forbidden"})

    monkeypatch.setattr(httpx, "post", mock_post)
    client = GitHubClient("token")
    with pytest.raises(GitHubClientError) as exc_info:
        client.trigger_repository_dispatch("owner", "repo")
    assert exc_info.value.status_code == 403


def test_trigger_dispatch_unexpected_status_raises(monkeypatch):
    from app.services.integration_service.github_client import GitHubClient, GitHubClientError

    def mock_post(url, **kwargs):
        return httpx.Response(500, json={"message": "Server error"})

    monkeypatch.setattr(httpx, "post", mock_post)
    client = GitHubClient("token")
    with pytest.raises(GitHubClientError):
        client.trigger_repository_dispatch("owner", "repo")
