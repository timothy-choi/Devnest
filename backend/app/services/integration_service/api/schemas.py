"""Request and response schemas for integration endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


# ── Provider token (OAuth connect) ───────────────────────────────────────────

class ProviderTokenResponse(BaseModel):
    token_id: int
    provider: str
    provider_username: str | None
    scopes: str
    created_at: datetime
    updated_at: datetime


class ProviderConnectStartResponse(BaseModel):
    authorization_url: str
    provider: str


# ── Workspace repository ──────────────────────────────────────────────────────

class ImportRepoRequest(BaseModel):
    repo_url: str = Field(..., max_length=1024, description="HTTPS clone URL of the repository")
    branch: str = Field("main", max_length=255)
    clone_dir: str = Field(
        "/workspace/project",
        max_length=1024,
        description="Absolute path inside the workspace container",
    )
    # Optional: use the authenticated token for the named provider ("github", "google").
    use_provider: str | None = Field(
        default=None, max_length=32,
        description="Provider name whose stored token to use for private repos",
    )


class ImportRepoResponse(BaseModel):
    repo_id: int
    workspace_id: int
    repo_url: str
    branch: str
    clone_dir: str
    clone_status: str
    job_id: int | None = None


class RepoStatusResponse(BaseModel):
    repo_id: int
    workspace_id: int
    repo_url: str
    branch: str
    clone_dir: str
    clone_status: str
    last_synced_at: datetime | None
    error_msg: str | None


# ── Git operations ────────────────────────────────────────────────────────────

class GitPullRequest(BaseModel):
    remote: str = Field("origin", max_length=128)
    branch: str | None = Field(None, max_length=255, description="Override the tracked branch")
    use_provider: str | None = Field(None, max_length=32)


class GitPushRequest(BaseModel):
    remote: str = Field("origin", max_length=128)
    branch: str | None = Field(None, max_length=255)
    use_provider: str | None = Field(None, max_length=32)
    force: bool = False


class GitOperationResponse(BaseModel):
    success: bool
    exit_code: int
    output: str
    operation: str  # "pull" | "push"
    repo_url: str | None = None


# ── CI/CD config + trigger ────────────────────────────────────────────────────

class CIConfigRequest(BaseModel):
    provider: str = Field("github_actions", max_length=64)
    repo_owner: str = Field(..., max_length=255)
    repo_name: str = Field(..., max_length=255)
    workflow_file: str = Field("main.yml", max_length=255)
    default_branch: str = Field("main", max_length=255)


class CIConfigResponse(BaseModel):
    ci_config_id: int
    workspace_id: int
    provider: str
    repo_owner: str
    repo_name: str
    workflow_file: str
    default_branch: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CITriggerRequest(BaseModel):
    event_type: str = Field("devnest_trigger", max_length=128)
    ref: str | None = Field(None, max_length=255)
    inputs: dict[str, Any] | None = None
    use_provider: str | None = Field(None, max_length=32)


class CITriggerResponse(BaseModel):
    trigger_id: int
    workspace_id: int
    status: str
    event_type: str
    ref: str | None
    triggered_at: datetime
    error_msg: str | None = None
