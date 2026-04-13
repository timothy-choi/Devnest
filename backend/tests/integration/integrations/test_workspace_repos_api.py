"""Integration tests for workspace repository import and Git sync routes."""

from __future__ import annotations

import pytest
from fastapi import status
from sqlmodel import select


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


def test_import_repo_requires_auth(client):
    resp = client.post("/workspaces/1/import-repo", json={"repo_url": "https://github.com/a/b.git"})
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_import_repo_unknown_workspace_404(client):
    token = _register_and_login(client, username="r1", email="r1@example.com")
    resp = client.post(
        "/workspaces/99999/import-repo",
        json={"repo_url": "https://github.com/a/b.git"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_import_repo_wrong_owner_403(client):
    token_a = _register_and_login(client, username="rowner", email="rowner@example.com")
    token_b = _register_and_login(client, username="rattacker", email="rattacker@example.com")
    ws_id = _create_workspace(client, token_a, name="ws_owner")

    resp = client.post(
        f"/workspaces/{ws_id}/import-repo",
        json={"repo_url": "https://github.com/a/b.git"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_import_repo_creates_pending_row_and_job(client, db_session):
    """POST /workspaces/{id}/import-repo creates a WorkspaceRepository row (status=pending)
    and enqueues a REPO_IMPORT worker job (202 Accepted)."""
    token = _register_and_login(client, username="importer", email="importer@example.com")
    ws_id = _create_workspace(client, token, name="ws_import")

    resp = client.post(
        f"/workspaces/{ws_id}/import-repo",
        json={
            "repo_url": "https://github.com/alice/myproject.git",
            "branch": "main",
            "clone_dir": "/workspace/myproject",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == status.HTTP_202_ACCEPTED
    data = resp.json()
    assert data["workspace_id"] == ws_id
    assert data["repo_url"] == "https://github.com/alice/myproject.git"
    assert data["clone_status"] == "pending"
    assert data["job_id"] is not None

    from app.services.integration_service.models import WorkspaceRepository
    from app.services.workspace_service.models.workspace_job import WorkspaceJob

    repo = db_session.exec(
        select(WorkspaceRepository).where(WorkspaceRepository.workspace_id == ws_id)
    ).first()
    assert repo is not None
    assert repo.clone_status == "pending"
    assert repo.provider == "github"

    job = db_session.get(WorkspaceJob, data["job_id"])
    assert job is not None
    assert job.job_type == "REPO_IMPORT"


def test_import_repo_second_call_409(client):
    """A second import call conflicts with the existing repo."""
    token = _register_and_login(client, username="imp2", email="imp2@example.com")
    ws_id = _create_workspace(client, token, name="ws_imp2")

    client.post(
        f"/workspaces/{ws_id}/import-repo",
        json={"repo_url": "https://github.com/a/b.git"},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = client.post(
        f"/workspaces/{ws_id}/import-repo",
        json={"repo_url": "https://github.com/a/c.git"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == status.HTTP_409_CONFLICT


def test_get_repo_status_after_import(client):
    token = _register_and_login(client, username="getrepo", email="getrepo@example.com")
    ws_id = _create_workspace(client, token, name="ws_getrepo")

    client.post(
        f"/workspaces/{ws_id}/import-repo",
        json={"repo_url": "https://github.com/a/b.git"},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = client.get(f"/workspaces/{ws_id}/repo", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["clone_status"] == "pending"


def test_get_repo_404_when_not_imported(client):
    token = _register_and_login(client, username="norepo", email="norepo@example.com")
    ws_id = _create_workspace(client, token, name="ws_norepo")
    resp = client.get(f"/workspaces/{ws_id}/repo", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_delete_repo_association(client):
    token = _register_and_login(client, username="delrepo", email="delrepo@example.com")
    ws_id = _create_workspace(client, token, name="ws_delrepo")

    client.post(
        f"/workspaces/{ws_id}/import-repo",
        json={"repo_url": "https://github.com/a/b.git"},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = client.delete(f"/workspaces/{ws_id}/repo", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == status.HTTP_204_NO_CONTENT

    # GET now returns 404.
    resp = client.get(f"/workspaces/{ws_id}/repo", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_git_pull_requires_cloned_repo(client):
    """Pull on a workspace with no repo returns 404; on a pending repo returns 409."""
    token = _register_and_login(client, username="pulltest", email="pulltest@example.com")
    ws_id = _create_workspace(client, token, name="ws_pull")

    # No repo at all → 404
    resp = client.post(
        f"/workspaces/{ws_id}/git/pull",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND

    # Import but don't clone → 409 (not RUNNING for runtime check first)
    client.post(
        f"/workspaces/{ws_id}/import-repo",
        json={"repo_url": "https://github.com/a/b.git"},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = client.post(
        f"/workspaces/{ws_id}/git/pull",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Workspace is not RUNNING → 409
    assert resp.status_code == status.HTTP_409_CONFLICT
