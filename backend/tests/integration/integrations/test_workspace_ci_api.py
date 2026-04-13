"""Integration tests for workspace CI/CD configuration and trigger routes."""

from __future__ import annotations

import pytest
import httpx
from fastapi import status


def _register_and_login(client, *, username, email, password="pass12345"):
    client.post("/auth/register", json={"username": username, "email": email, "password": password})
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == status.HTTP_200_OK
    return resp.json()["access_token"]


def _create_workspace(client, token, *, name="ws1"):
    resp = client.post(
        "/workspaces",
        json={"name": name, "description": "test", "config": {"image": "nginx:alpine"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code in (201, 202)
    return resp.json()["workspace_id"]


def test_get_ci_config_404_when_not_set(client):
    token = _register_and_login(client, username="cinone", email="cinone@example.com")
    ws_id = _create_workspace(client, token, name="ci_none")
    resp = client.get(f"/workspaces/{ws_id}/ci/config", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_create_ci_config(client):
    token = _register_and_login(client, username="cicreate", email="cicreate@example.com")
    ws_id = _create_workspace(client, token, name="ci_create")
    resp = client.post(
        f"/workspaces/{ws_id}/ci/config",
        json={
            "provider": "github_actions",
            "repo_owner": "myorg",
            "repo_name": "myrepo",
            "workflow_file": "ci.yml",
            "default_branch": "main",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    assert data["repo_owner"] == "myorg"
    assert data["repo_name"] == "myrepo"
    assert data["is_active"] is True


def test_upsert_ci_config_updates_existing(client):
    token = _register_and_login(client, username="ciup", email="ciup@example.com")
    ws_id = _create_workspace(client, token, name="ci_up")

    client.post(
        f"/workspaces/{ws_id}/ci/config",
        json={"provider": "github_actions", "repo_owner": "org", "repo_name": "repo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = client.post(
        f"/workspaces/{ws_id}/ci/config",
        json={"provider": "github_actions", "repo_owner": "org", "repo_name": "updated-repo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Updates return 200; 201 is only for initial creation.
    assert resp.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED)
    assert resp.json()["repo_name"] == "updated-repo"


def test_delete_ci_config(client):
    token = _register_and_login(client, username="cidel", email="cidel@example.com")
    ws_id = _create_workspace(client, token, name="ci_del")

    client.post(
        f"/workspaces/{ws_id}/ci/config",
        json={"provider": "github_actions", "repo_owner": "org", "repo_name": "repo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = client.delete(f"/workspaces/{ws_id}/ci/config", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == status.HTTP_204_NO_CONTENT

    resp = client.get(f"/workspaces/{ws_id}/ci/config", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_trigger_ci_without_config_404(client):
    token = _register_and_login(client, username="cinoconf", email="cinoconf@example.com")
    ws_id = _create_workspace(client, token, name="ci_no_conf")
    resp = client.post(
        f"/workspaces/{ws_id}/ci/trigger",
        json={"event_type": "devnest_trigger"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_trigger_ci_without_provider_token_400(client, db_session):
    """Trigger without a GitHub provider token returns 400."""
    token = _register_and_login(client, username="cinoprov", email="cinoprov@example.com")
    ws_id = _create_workspace(client, token, name="ci_no_prov")

    client.post(
        f"/workspaces/{ws_id}/ci/config",
        json={"provider": "github_actions", "repo_owner": "org", "repo_name": "repo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = client.post(
        f"/workspaces/{ws_id}/ci/trigger",
        json={"event_type": "devnest_trigger"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "provider" in resp.json()["detail"].lower() or "token" in resp.json()["detail"].lower()


def test_trigger_ci_with_github_token_calls_api(client, db_session, monkeypatch):
    """With a stored GitHub token, trigger calls the GitHub API."""
    from datetime import datetime, timezone
    from app.services.integration_service.models import UserProviderToken
    from app.services.integration_service.token_crypto import encrypt_token, invalidate_cache
    from app.services.auth_service.models import UserAuth
    from sqlmodel import select

    monkeypatch.setenv("DEVNEST_TOKEN_ENCRYPTION_KEY", "integ-test-key-ci")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()
    invalidate_cache()

    # Intercept GitHub API call.
    dispatch_called = []
    original_post = httpx.post

    def mock_dispatch(url, **kwargs):
        dispatch_called.append(url)
        return httpx.Response(204, content=b"")

    monkeypatch.setattr(httpx, "post", mock_dispatch)

    token = _register_and_login(client, username="citrig", email="citrig@example.com")
    ws_id = _create_workspace(client, token, name="ci_trig")

    # Seed provider token.
    user = db_session.exec(select(UserAuth).where(UserAuth.username == "citrig")).first()
    assert user is not None
    now = datetime.now(timezone.utc)
    ptoken = UserProviderToken(
        user_id=user.user_auth_id,
        provider="github",
        access_token_encrypted=encrypt_token("ghp_test123"),
        scopes="repo",
        provider_user_id="999",
        provider_username="citrig",
        created_at=now,
        updated_at=now,
    )
    db_session.add(ptoken)
    db_session.commit()

    # Set CI config.
    client.post(
        f"/workspaces/{ws_id}/ci/config",
        json={"provider": "github_actions", "repo_owner": "myorg", "repo_name": "myrepo"},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Trigger.
    resp = client.post(
        f"/workspaces/{ws_id}/ci/trigger",
        json={"event_type": "devnest_trigger"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    assert data["status"] == "succeeded"
    assert any("myorg/myrepo" in url for url in dispatch_called)

    invalidate_cache()
    get_settings.cache_clear()


def test_upsert_ci_config_second_call_returns_200(client):
    """Second POST to /ci/config is an update and returns 200 (not 201)."""
    token = _register_and_login(client, username="ciup200", email="ciup200@example.com")
    ws_id = _create_workspace(client, token, name="ci_up200")

    client.post(
        f"/workspaces/{ws_id}/ci/config",
        json={"provider": "github_actions", "repo_owner": "org", "repo_name": "repo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = client.post(
        f"/workspaces/{ws_id}/ci/config",
        json={"provider": "github_actions", "repo_owner": "org", "repo_name": "updated-repo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["repo_name"] == "updated-repo"


def test_ci_trigger_inputs_nested_under_inputs_key(client, db_session, monkeypatch):
    """Inputs sent by the caller are nested under 'inputs' in the GitHub payload so
    they cannot overwrite trusted fields like workspace_id and ref."""
    from datetime import datetime, timezone
    from app.services.integration_service.models import UserProviderToken
    from app.services.integration_service.token_crypto import encrypt_token, invalidate_cache
    from app.services.auth_service.models import UserAuth
    from sqlmodel import select

    monkeypatch.setenv("DEVNEST_TOKEN_ENCRYPTION_KEY", "ci-nesting-test-key")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()
    invalidate_cache()

    dispatched_payloads = []

    def mock_post(url, **kwargs):
        if "dispatches" in url:
            dispatched_payloads.append(kwargs.get("json", {}))
            return httpx.Response(204, content=b"")
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx, "post", mock_post)

    token = _register_and_login(client, username="cinest", email="cinest@example.com")
    ws_id = _create_workspace(client, token, name="ci_nest")

    user = db_session.exec(select(UserAuth).where(UserAuth.username == "cinest")).first()
    now = datetime.now(timezone.utc)
    db_session.add(UserProviderToken(
        user_id=user.user_auth_id,
        provider="github",
        access_token_encrypted=encrypt_token("ghp_nest"),
        scopes="repo",
        provider_user_id="77",
        provider_username="cinest",
        created_at=now,
        updated_at=now,
    ))
    db_session.commit()

    client.post(
        f"/workspaces/{ws_id}/ci/config",
        json={"provider": "github_actions", "repo_owner": "org", "repo_name": "myrepo"},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = client.post(
        f"/workspaces/{ws_id}/ci/trigger",
        json={"event_type": "deploy", "inputs": {"workspace_id": "evil", "ref": "evil"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == status.HTTP_201_CREATED

    assert len(dispatched_payloads) == 1
    payload = dispatched_payloads[0]["client_payload"]
    # Top-level fields must be the trusted server values (integers/strings from the system).
    assert payload["workspace_id"] == ws_id
    assert payload["ref"] != "evil"
    # User inputs are safely nested.
    assert payload["inputs"]["workspace_id"] == "evil"
    assert payload["inputs"]["ref"] == "evil"

    invalidate_cache()
    get_settings.cache_clear()


def test_list_ci_triggers(client, db_session, monkeypatch):
    from datetime import datetime, timezone
    from app.services.integration_service.models import UserProviderToken
    from app.services.integration_service.token_crypto import encrypt_token, invalidate_cache
    from app.services.auth_service.models import UserAuth
    from sqlmodel import select

    monkeypatch.setenv("DEVNEST_TOKEN_ENCRYPTION_KEY", "list-triggers-key")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()
    invalidate_cache()

    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(204, content=b""))

    token = _register_and_login(client, username="cilist", email="cilist@example.com")
    ws_id = _create_workspace(client, token, name="ci_list")

    user = db_session.exec(select(UserAuth).where(UserAuth.username == "cilist")).first()
    now = datetime.now(timezone.utc)
    ptoken = UserProviderToken(
        user_id=user.user_auth_id,
        provider="github",
        access_token_encrypted=encrypt_token("ghp_list_token"),
        scopes="repo",
        provider_user_id="888",
        provider_username="cilist",
        created_at=now,
        updated_at=now,
    )
    db_session.add(ptoken)
    db_session.commit()

    client.post(
        f"/workspaces/{ws_id}/ci/config",
        json={"provider": "github_actions", "repo_owner": "org", "repo_name": "repo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    client.post(
        f"/workspaces/{ws_id}/ci/trigger",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    client.post(
        f"/workspaces/{ws_id}/ci/trigger",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = client.get(f"/workspaces/{ws_id}/ci/triggers", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == status.HTTP_200_OK
    assert len(resp.json()) == 2

    invalidate_cache()
    get_settings.cache_clear()
