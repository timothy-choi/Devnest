"""Request and response schemas for integration endpoints."""

from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, HttpUrl, field_validator


def _validate_clone_url(v: str) -> str:
    """Reject non-HTTPS schemes and RFC-1918 / loopback targets."""
    parsed = urlparse(v)
    if parsed.scheme not in ("https", "git+https"):
        raise ValueError("repo_url must use https:// or git+https:// scheme")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("repo_url must include a valid hostname")
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            raise ValueError("repo_url hostname must not be a private/loopback address")
    except ValueError as exc:
        if "repo_url" in str(exc):
            raise
        # hostname is a domain name — allowed; IP parsing failed because it's DNS
    return v


def _validate_clone_dir(v: str) -> str:
    """Reject path traversal attempts."""
    if ".." in v.split("/"):
        raise ValueError("clone_dir must not contain '..' components")
    if not v.startswith("/"):
        raise ValueError("clone_dir must be an absolute path")
    return v


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

    @field_validator("repo_url")
    @classmethod
    def validate_repo_url(cls, v: str) -> str:
        return _validate_clone_url(v)

    @field_validator("clone_dir")
    @classmethod
    def validate_clone_dir(cls, v: str) -> str:
        return _validate_clone_dir(v)


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
