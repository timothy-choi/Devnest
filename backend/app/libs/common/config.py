import os
from functools import lru_cache
from typing import Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    # When true, startup aborts if jwt_secret_key is the default placeholder value.
    # Set DEVNEST_REQUIRE_SECRETS=true in staging and production environments.
    # A loud WARNING is always emitted when the default secret is detected regardless of this flag.
    devnest_require_secrets: bool = False
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14
    # Opaque workspace session tokens (attach → access); hashed at rest with HMAC-SHA256(jwt_secret_key, token).
    workspace_session_ttl_seconds: int = 86400
    # Workspace job worker: bounded retries (per job row) before terminal FAILED + workspace ERROR.
    workspace_job_max_attempts: int = 2
    workspace_job_retry_backoff_seconds: int = 15
    password_reset_token_expire_minutes: int = 60
    # If true, PUT /auth/forgot-password includes reset_token in JSON (local/testing only; use email in production).
    password_reset_return_token: bool = False

    # OAuth redirect bases (no trailing slash). Env may be host:port only; code prepends http:// if needed.
    github_oauth_public_base_url: str = ""
    gcloud_oauth_public_base_url: str = ""
    oauth_github_client_id: str = ""
    oauth_github_client_secret: str = ""
    oauth_google_client_id: str = ""
    oauth_google_client_secret: str = ""

    # --- Internal platform auth (sensitive; header X-Internal-API-Key) ---
    # Legacy single key: used for any internal surface whose scope-specific key is unset.
    # Prefer per-scope keys in production so workers, autoscaler, infra automation, etc. do not share one secret.
    internal_api_key: str = ""
    internal_api_key_workspace_jobs: str = ""
    internal_api_key_workspace_reconcile: str = ""
    internal_api_key_autoscaler: str = ""
    internal_api_key_infrastructure: str = ""
    internal_api_key_notifications: str = ""
    # When > 0, every non-empty internal API secret (legacy + scoped) must be at least this many characters.
    # Default 0 disables (local/CI). Production: set via DEVNEST_INTERNAL_API_KEY_MIN_LENGTH (e.g. 24).
    devnest_internal_api_key_min_length: int = 0

    # Workspace orchestrator (Docker): image for workspace containers; empty falls back to env then nginx:alpine.
    workspace_container_image: str = ""
    # Host directory root for per-workspace project bind mounts; empty uses system temp / devnest-workspaces.
    workspace_projects_base: str = ""
    # Root directory for snapshot archives (local filesystem provider). Empty → system temp / devnest-snapshots.
    devnest_snapshot_storage_root: str = ""
    # Snapshot storage backend: "local" (default) or "s3".
    devnest_snapshot_storage_provider: str = "local"
    # S3 provider settings (only used when devnest_snapshot_storage_provider=s3).
    devnest_s3_snapshot_bucket: str = ""
    devnest_s3_snapshot_prefix: str = "devnest-snapshots"
    # Temp directory for staging S3 snapshot archives locally. Empty → system temp.
    devnest_snapshot_temp_dir: str = ""

    # Standalone gateway route-admin (data plane): register/deregister workspace routes after orchestration.
    # DEVNEST_GATEWAY_URL is the route-admin HTTP base (not Traefik's public :80). Default matches compose
    # DEVNEST_ROUTE_ADMIN_PORT=9080.
    devnest_gateway_url: str = "http://127.0.0.1:9080"
    devnest_base_domain: str = "app.devnest.local"
    devnest_gateway_enabled: bool = False
    # Used for gateway_url hint on attach/access when route registration is enabled (no TLS in V1).
    devnest_gateway_public_scheme: str = "http"
    # When true, GET /internal/gateway/auth enforces workspace session validation for Traefik ForwardAuth.
    # Set false in local/dev mode (default) to skip session requirement during development.
    devnest_gateway_auth_enabled: bool = False

    # Outbound notification email (optional). If smtp_host is empty, the email channel stays in stub mode.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_address: str = ""
    smtp_use_tls: bool = True

    @field_validator("smtp_use_tls", mode="before")
    @classmethod
    def _parse_smtp_use_tls(cls, v):  # noqa: ANN001 — pydantic coerces env strings
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    @field_validator("devnest_gateway_enabled", "devnest_gateway_auth_enabled", mode="before")
    @classmethod
    def _parse_devnest_gateway_enabled(cls, v):  # noqa: ANN001
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    # AWS (EC2 node registry; optional — uses default credential chain when keys empty).
    aws_region: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    # Placement filter: ``all`` (default) = local + EC2 nodes; ``local`` / ``ec2`` = restrict pool.
    devnest_node_provider: str = "all"
    # Default SSH user when registering EC2 nodes (Amazon Linux / Ubuntu images).
    devnest_ec2_ssh_user_default: str = "ubuntu"
    # Default ``ExecutionNode.execution_mode`` for new EC2 registry rows (``ssm_docker`` preferred; ``ssh_docker`` fallback).
    devnest_ec2_default_execution_mode: str = "ssm_docker"
    # Optional worker override: ``local`` = force worker-local Docker for local provider nodes only;
    # ``ssm`` = force SSM path for EC2 nodes only; empty = use each node's ``execution_mode``.
    devnest_execution_mode: str = ""

    @field_validator("devnest_ec2_default_execution_mode", mode="before")
    @classmethod
    def _normalize_devnest_ec2_default_execution_mode(cls, v):  # noqa: ANN001
        s = str(v or "").strip().lower()
        if s in ("ssh_docker", "ssm_docker"):
            return s
        return "ssm_docker"

    @field_validator("devnest_execution_mode", mode="before")
    @classmethod
    def _normalize_devnest_execution_mode(cls, v):  # noqa: ANN001
        s = str(v or "").strip().lower()
        if s in ("", "local", "ssm"):
            return s
        return ""

    @field_validator("devnest_node_provider", mode="before")
    @classmethod
    def _normalize_devnest_node_provider(cls, v):  # noqa: ANN001
        if v is None:
            return "all"
        s = str(v).strip().lower()
        if s in ("", "any", "*"):
            return "all"
        if s in ("local", "ec2", "all"):
            return s
        return "all"

    # EC2 provisioning defaults (explicit; empty AMI/subnet/SG means provision CLI/API must pass overrides).
    devnest_ec2_ami_id: str = ""
    devnest_ec2_instance_type: str = "t3.medium"
    devnest_ec2_subnet_id: str = ""
    # Comma-separated security group ids for ``run_instances`` (VPC).
    devnest_ec2_security_group_ids: str = ""
    # IAM instance profile **name** (not ARN) attached to new instances (SSM agent / instance role).
    devnest_ec2_instance_profile: str = ""
    # Optional EC2 key pair name (prefer SSM + instance role; keys are often unnecessary).
    devnest_ec2_key_name: str = ""
    # Prefix for ``{prefix}:managed`` and ``{prefix}:node_key`` tags on provisioned instances.
    devnest_ec2_tag_prefix: str = "devnest"

    # ── Built-in background job worker ──────────────────────────────────────────
    # When true, the FastAPI process runs the job poll loop in an asyncio background
    # task (no separate worker process required). Disabled by default so existing
    # deployments that use the standalone `workspace_job_poll_loop` process or the
    # POST /internal/workspace-jobs/process endpoint are unaffected.
    devnest_worker_enabled: bool = False
    # Seconds between job poll ticks. Values below 1 are coerced to 1.
    devnest_worker_poll_interval_seconds: int = 5
    # Max workspace jobs to dequeue and process per tick.
    devnest_worker_batch_size: int = 5

    # Autoscaler (V1): fleet-level EC2 capacity; off by default for safe local/dev behavior.
    devnest_autoscaler_enabled: bool = False
    # When set with ``devnest_autoscaler_enabled``, worker triggers one EC2 provision on NoSchedulableNodeError.
    devnest_autoscaler_provision_on_no_capacity: bool = False
    devnest_autoscaler_max_concurrent_provisioning: int = 3
    # Do not reclaim EC2 nodes unless at least this many READY+schedulable EC2 nodes exist (last-node safety).
    # Values below 2 are coerced to 2 so scale-down cannot target the sole READY EC2 node via misconfiguration.
    devnest_autoscaler_min_ec2_nodes_before_reclaim: int = 2

    @field_validator("devnest_worker_enabled", mode="before")
    @classmethod
    def _parse_devnest_worker_enabled(cls, v):  # noqa: ANN001
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    @field_validator("devnest_worker_poll_interval_seconds", mode="before")
    @classmethod
    def _coerce_worker_poll_interval(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 5
        return max(1, min(n, 3600))

    @field_validator("devnest_worker_batch_size", mode="before")
    @classmethod
    def _coerce_worker_batch_size(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 5
        return max(1, min(n, 50))

    @field_validator("devnest_autoscaler_enabled", "devnest_autoscaler_provision_on_no_capacity", mode="before")
    @classmethod
    def _parse_autoscaler_flags(cls, v):  # noqa: ANN001
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    @field_validator("devnest_autoscaler_max_concurrent_provisioning", mode="before")
    @classmethod
    def _autoscaler_max_prov(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 3
        return max(1, min(n, 50))

    @field_validator("devnest_autoscaler_min_ec2_nodes_before_reclaim", mode="before")
    @classmethod
    def _autoscaler_min_ec2(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 2
        return max(2, min(n, 100))

    @field_validator("devnest_internal_api_key_min_length", mode="before")
    @classmethod
    def _coerce_internal_api_key_min_length(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 0
        return max(0, min(n, 512))

    @field_validator("devnest_require_secrets", mode="before")
    @classmethod
    def _parse_devnest_require_secrets(cls, v):  # noqa: ANN001
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    @model_validator(mode="after")
    def _validate_jwt_secret(self) -> Self:
        """Warn (always) or abort (when DEVNEST_REQUIRE_SECRETS=true) if the JWT secret is insecure."""
        import logging  # noqa: PLC0415

        _DEFAULT = "change-me-in-production"
        if self.jwt_secret_key == _DEFAULT:
            logging.getLogger(__name__).warning(
                "SECURITY WARNING: jwt_secret_key is set to the default placeholder value "
                "'change-me-in-production'. This is insecure. Set the JWT_SECRET_KEY "
                "environment variable to a strong, random value before deploying to production."
            )
            if self.devnest_require_secrets:
                raise ValueError(
                    "Insecure startup rejected: jwt_secret_key must not be the default value "
                    "'change-me-in-production'. "
                    "Set the JWT_SECRET_KEY environment variable to a cryptographically strong "
                    "random string (e.g. `openssl rand -hex 32`). "
                    "To disable this guard in non-production environments, set "
                    "DEVNEST_REQUIRE_SECRETS=false."
                )
        return self

    @model_validator(mode="after")
    def _validate_internal_api_secret_lengths(self) -> Self:
        min_len = int(self.devnest_internal_api_key_min_length or 0)
        if min_len <= 0:
            return self
        from app.libs.security.internal_auth import INTERNAL_API_SECRET_FIELD_NAMES

        for fname in INTERNAL_API_SECRET_FIELD_NAMES:
            raw = str(getattr(self, fname, "") or "").strip()
            if raw and len(raw) < min_len:
                msg = f"{fname} length {len(raw)} < devnest_internal_api_key_min_length ({min_len})"
                raise ValueError(msg)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
