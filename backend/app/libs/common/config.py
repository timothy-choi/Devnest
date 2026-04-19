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
    # Active runtime environment. Accepted values: "development", "staging", "production".
    # When set to any value other than "development", startup aborts if jwt_secret_key is the
    # default placeholder. This provides automatic enforcement without requiring
    # DEVNEST_REQUIRE_SECRETS=true to be set manually in every non-dev environment.
    devnest_env: str = "development"
    # When true, startup aborts if jwt_secret_key is the default placeholder value regardless of
    # DEVNEST_ENV. Set DEVNEST_REQUIRE_SECRETS=true in staging and production environments.
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

    # Workspace orchestrator (Docker): image for workspace containers; empty falls back to
    # DEVNEST_WORKSPACE_CONTAINER_IMAGE / DEVNEST_WORKSPACE_IMAGE then devnest/workspace:latest (see app_factory).
    workspace_container_image: str = ""
    # Host directory root for per-workspace project bind mounts; empty uses system temp / devnest-workspaces.
    # When the API/worker runs in Docker with only docker.sock mounted, that default is /tmp/... *inside*
    # the control plane container — mkdir/chown there do not fix host bind sources → set WORKSPACE_PROJECTS_BASE
    # to a path mounted from the host (see docker-compose.integration.yml).
    workspace_projects_base: str = ""
    # When true, startup removes workspace project directories under ``workspace_projects_base`` that
    # are no longer referenced by any current ``workspace`` row. Intended for ephemeral integration /
    # EC2 restart flows where the DB resets but the host filesystem persists.
    devnest_workspace_projects_prune_orphans_on_startup: bool = False
    # Root directory for snapshot archives (local filesystem provider). Empty → system temp / devnest-snapshots.
    devnest_snapshot_storage_root: str = ""
    # Snapshot storage backend: "local" (default) or "s3".
    devnest_snapshot_storage_provider: str = "local"
    # S3 provider settings (only used when devnest_snapshot_storage_provider=s3).
    devnest_s3_snapshot_bucket: str = ""
    devnest_s3_snapshot_prefix: str = "devnest-snapshots"
    # Temp directory for staging S3 snapshot archives locally. Empty → system temp.
    devnest_snapshot_temp_dir: str = ""

    # ── Integration / provider token encryption ──────────────────────────────
    # Key used to Fernet-encrypt stored OAuth provider tokens (GitHub, Google).
    # If empty, the JWT secret key is used as the derivation input (not recommended for production).
    # Set to a strong random value in production: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    devnest_token_encryption_key: str = ""

    # Terminal WebSocket: shell to launch in workspace containers (default /bin/bash, fallback /bin/sh).
    devnest_workspace_shell: str = "/bin/bash"
    # Terminal WebSocket: default PTY dimensions.
    devnest_terminal_default_cols: int = 200
    devnest_terminal_default_rows: int = 50

    # ── Standalone gateway route-admin (data plane) ───────────────────────────
    # register/deregister workspace routes after orchestration.
    # DEVNEST_GATEWAY_URL is the route-admin HTTP base (not Traefik's public :80). Default matches compose
    # DEVNEST_ROUTE_ADMIN_PORT=9080.
    devnest_gateway_url: str = "http://127.0.0.1:9080"
    devnest_base_domain: str = "app.devnest.local"
    devnest_gateway_enabled: bool = False
    # Used for gateway_url hint on attach/access when route registration is enabled (no TLS in V1).
    devnest_gateway_public_scheme: str = "http"
    # When Traefik is published on a non-default port, set this so ``gateway_url`` matches the browser
    # (standard 80/443 are omitted from the URL). Example: map ``9081:80`` and set ``9081`` here.
    devnest_gateway_public_port: int = 0
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

    @field_validator(
        "devnest_gateway_enabled",
        "devnest_gateway_auth_enabled",
        "devnest_workspace_projects_prune_orphans_on_startup",
        "devnest_reconcile_enabled",
        "devnest_topology_janitor_enabled",
        "devnest_rate_limit_enabled",
        "devnest_metrics_auth_enabled",
        "devnest_workspace_http_probe_enabled",
        "devnest_require_ide_http_probe",
        "devnest_allow_runtime_env_fallback",
        "devnest_require_prod_reconcile_locking",
        "devnest_probe_assume_colocated_engine",
        mode="before",
    )
    @classmethod
    def _parse_devnest_gateway_enabled(cls, v):  # noqa: ANN001
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    @field_validator("devnest_gateway_public_port", mode="before")
    @classmethod
    def _coerce_gateway_public_port(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 0
        return max(0, min(n, 65535))

    @field_validator("devnest_reconcile_interval_seconds", mode="before")
    @classmethod
    def _coerce_reconcile_interval(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 30
        return max(10, min(n, 3600))

    @field_validator("devnest_reconcile_batch_size", mode="before")
    @classmethod
    def _coerce_reconcile_batch_size(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 10
        return max(1, min(n, 100))

    @field_validator("devnest_reconcile_lease_ttl_seconds", mode="before")
    @classmethod
    def _coerce_reconcile_lease_ttl(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 120
        return max(30, min(n, 3600))

    @field_validator("devnest_topology_janitor_stale_seconds", mode="before")
    @classmethod
    def _coerce_topology_janitor_stale(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 600
        return max(30, min(n, 86400))

    @field_validator("workspace_job_stuck_timeout_seconds", mode="before")
    @classmethod
    def _coerce_stuck_timeout(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 300
        return max(0, min(n, 86400))

    @field_validator("devnest_rate_limit_auth_per_minute", "devnest_rate_limit_sse_per_minute", mode="before")
    @classmethod
    def _coerce_rate_limit(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 20
        return max(1, min(n, 10000))

    @field_validator("devnest_sse_poll_interval_seconds", mode="before")
    @classmethod
    def _coerce_sse_poll_interval(cls, v):  # noqa: ANN001
        try:
            n = float(v)
        except (TypeError, ValueError):
            return 2.0
        return max(0.5, min(n, 60.0))

    @field_validator("devnest_workspace_bringup_ide_tcp_wait_seconds", mode="before")
    @classmethod
    def _coerce_bringup_ide_tcp_wait(cls, v):  # noqa: ANN001
        try:
            n = float(v)
        except (TypeError, ValueError):
            return 90.0
        return max(1.0, min(n, 600.0))

    @field_validator("devnest_workspace_bringup_ide_tcp_poll_interval_seconds", mode="before")
    @classmethod
    def _coerce_bringup_ide_tcp_poll_interval(cls, v):  # noqa: ANN001
        try:
            n = float(v)
        except (TypeError, ValueError):
            return 1.5
        return max(0.05, min(n, 30.0))

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

    # ── Automated reconcile loop ─────────────────────────────────────────────
    # When true, the FastAPI process runs a background reconcile tick that enqueues
    # RECONCILE_RUNTIME jobs for workspaces in the target statuses.
    devnest_reconcile_enabled: bool = False
    # Seconds between reconcile ticks. Values below 10 are coerced to 10.
    devnest_reconcile_interval_seconds: int = 30
    # Max workspaces to reconcile per tick.
    devnest_reconcile_batch_size: int = 10
    # Comma-separated workspace statuses to target for reconcile (default: RUNNING,ERROR).
    devnest_reconcile_target_statuses: str = "RUNNING,ERROR"
    # If a RECONCILE_RUNTIME job has been RUNNING longer than this many seconds it is
    # considered stale (crashed worker) and a new reconcile may be enqueued. Default 120s.
    devnest_reconcile_lease_ttl_seconds: int = 120
    # Topology janitor (stuck attachments / orphan IP leases) runs at the start of reconcile when enabled.
    devnest_topology_janitor_enabled: bool = True
    devnest_topology_janitor_stale_seconds: int = 600

    # ── Worker stuck-job reclaim ──────────────────────────────────────────────
    # If a job has been in RUNNING state longer than this many seconds it is presumed
    # orphaned by a crashed worker and is reclaimed (retry or terminal failure).
    # Set to 0 to disable reclaim. Default 300s (5 min).
    workspace_job_stuck_timeout_seconds: int = 300

    # ── Metrics endpoint protection ──────────────────────────────────────────────
    # When true, GET /metrics requires the X-Internal-API-Key header validated against
    # the INFRASTRUCTURE scope key (or legacy INTERNAL_API_KEY fallback).
    # Default false to preserve backward compatibility (protect at ingress in dev/local).
    # Set true in production when Prometheus scraper can supply the key.
    devnest_metrics_auth_enabled: bool = False

    # ── Rate limiting ─────────────────────────────────────────────────────────
    # Globally enable / disable in-process rate limiting. Default true.
    devnest_rate_limit_enabled: bool = True
    # Max requests per minute per IP for auth endpoints (login, register, forgot-password).
    devnest_rate_limit_auth_per_minute: int = 20
    # Max requests per minute per IP for the SSE event-stream endpoint.
    devnest_rate_limit_sse_per_minute: int = 30

    # ── SSE event delivery ────────────────────────────────────────────────────
    # How often (seconds) the SSE polling loop wakes up to check for new DB events.
    # This is the maximum latency for cross-worker event delivery in multi-process deployments
    # (gunicorn workers do not share the in-process event bus; they all poll DB instead).
    # Range: [0.5, 60]. Default 2.0.
    devnest_sse_poll_interval_seconds: float = 2.0

    # After TCP connect succeeds on the workspace IDE port, perform HTTP GET to verify code-server
    # (or equivalent) is serving. Default true in production. Set false for environments where the
    # workspace IP is not routable from the API host (e.g. integration/system tests with DB-only
    # topology addresses); tests set DEVNEST_WORKSPACE_HTTP_PROBE_ENABLED=false via conftest.
    devnest_workspace_http_probe_enabled: bool = True
    # When true (default), RUNNING / healthy workspace probes must pass HTTP IDE readiness where
    # ``devnest_workspace_http_probe_enabled`` is also true. Staging/production require both.
    devnest_require_ide_http_probe: bool = True
    # HTTP path for IDE readiness (code-server exposes /healthz in typical installs). Must start with ``/``.
    devnest_workspace_ide_health_path: str = "/healthz"
    # After topology attach, poll TCP to ``workspace_ip:IDE`` for up to this many seconds before failing
    # bring-up. code-server often needs tens of seconds (extensions, disk) before ``nc`` succeeds.
    devnest_workspace_bringup_ide_tcp_wait_seconds: float = 90.0
    # Sleep between TCP poll attempts during bring-up (see ``devnest_workspace_bringup_ide_tcp_wait_seconds``).
    devnest_workspace_bringup_ide_tcp_poll_interval_seconds: float = 1.5
    # When true (default), TCP/HTTP probes may run from the API/worker process (same host as Docker).
    # Set false on control-plane hosts that are not co-located with workspace Docker (e.g. API-only
    # tier); then probes require ``NodeExecutionBundle.service_reachability_runner`` (SSH/SSM) so
    # checks run on the execution node.
    devnest_probe_assume_colocated_engine: bool = True

    # Authoritative placement: allow legacy DEVNEST_NODE_ID / DEVNEST_TOPOLOGY_ID resolution in
    # development only. Must remain false in staging/production.
    devnest_allow_runtime_env_fallback: bool = False

    # Reconcile duplicate-suppression: ``postgres_advisory`` uses pg_try_advisory_lock (required in
    # staging/production with PostgreSQL). ``portable`` is SQLite/single-writer only — rejected
    # for production when ``devnest_require_prod_reconcile_locking`` is true.
    devnest_reconcile_lock_backend: str = "postgres_advisory"
    devnest_require_prod_reconcile_locking: bool = True

    # Autoscaler (V1): fleet-level EC2 capacity; off by default for safe local/dev behavior.
    devnest_autoscaler_enabled: bool = False
    # When set with ``devnest_autoscaler_enabled``, worker triggers one EC2 provision on NoSchedulableNodeError.
    devnest_autoscaler_provision_on_no_capacity: bool = False
    devnest_autoscaler_max_concurrent_provisioning: int = 3
    # Do not reclaim EC2 nodes unless at least this many READY+schedulable EC2 nodes exist (last-node safety).
    # Values below 2 are coerced to 2 so scale-down cannot target the sole READY EC2 node via misconfiguration.
    devnest_autoscaler_min_ec2_nodes_before_reclaim: int = 2
    # Drain delay: minimum seconds to wait after a node is marked DRAINING before allowing termination.
    # Prevents premature scale-down on nodes that still have recently-started workloads.
    devnest_autoscaler_drain_delay_seconds: int = 30
    # Recent-activity window: a node is considered "recently active" if a workspace was started/stopped
    # within this many seconds of the scale-down evaluation. Default 300s (5 min).
    devnest_autoscaler_recent_activity_window_seconds: int = 300

    # ── Distributed rate limiting (Redis) ───────────────────────────────────
    # Rate limit backend: "memory" (default, single-process) or "redis" (distributed).
    # When "redis", DEVNEST_REDIS_URL must be set.
    devnest_rate_limit_backend: str = "memory"
    # Redis connection URL for distributed rate limiting (e.g. redis://localhost:6379/0).
    devnest_redis_url: str = ""
    # When true, startup aborts if devnest_rate_limit_backend=redis but devnest_redis_url is empty.
    devnest_require_distributed_rate_limiting: bool = False

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

    @field_validator(
        "devnest_autoscaler_enabled",
        "devnest_autoscaler_provision_on_no_capacity",
        "devnest_require_distributed_rate_limiting",
        mode="before",
    )
    @classmethod
    def _parse_autoscaler_flags(cls, v):  # noqa: ANN001
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    @field_validator("devnest_autoscaler_drain_delay_seconds", mode="before")
    @classmethod
    def _coerce_drain_delay(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 30
        return max(0, min(n, 3600))

    @field_validator("devnest_autoscaler_recent_activity_window_seconds", mode="before")
    @classmethod
    def _coerce_activity_window(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 300
        return max(0, min(n, 86400))

    @field_validator("devnest_rate_limit_backend", mode="before")
    @classmethod
    def _normalize_rate_limit_backend(cls, v):  # noqa: ANN001
        s = str(v or "memory").strip().lower()
        return s if s in ("memory", "redis") else "memory"

    @field_validator("devnest_reconcile_lock_backend", mode="before")
    @classmethod
    def _normalize_reconcile_lock_backend(cls, v):  # noqa: ANN001
        s = str(v or "postgres_advisory").strip().lower()
        return s if s in ("postgres_advisory", "portable") else "postgres_advisory"

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

    @field_validator("devnest_env", mode="before")
    @classmethod
    def _normalize_devnest_env(cls, v):  # noqa: ANN001
        s = str(v or "development").strip().lower()
        if s in ("development", "dev", "local", "test"):
            return "development"
        if s in ("staging", "stage"):
            return "staging"
        if s in ("production", "prod"):
            return "production"
        return s

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
        """Warn (always) or abort (when insecure secret is detected in non-dev env) if JWT secret is insecure.

        Abort conditions (raise ``RuntimeError`` to surface clearly at startup):
          1. ``DEVNEST_REQUIRE_SECRETS=true`` — explicit opt-in regardless of environment.
          2. ``DEVNEST_ENV`` is set to a value other than ``"development"`` — automatic enforcement
             for staging and production environments without requiring a separate flag.
        """
        import logging  # noqa: PLC0415

        _DEFAULT = "change-me-in-production"
        if self.jwt_secret_key != _DEFAULT:
            return self

        logging.getLogger(__name__).warning(
            "SECURITY WARNING: jwt_secret_key is set to the default placeholder value "
            "'change-me-in-production'. This is insecure. Set the JWT_SECRET_KEY "
            "environment variable to a strong, random value before deploying to production."
        )

        env = str(self.devnest_env or "development").strip().lower()
        is_non_dev_env = env != "development"
        if self.devnest_require_secrets or is_non_dev_env:
            context = (
                f"DEVNEST_ENV={env!r}"
                if is_non_dev_env and not self.devnest_require_secrets
                else "DEVNEST_REQUIRE_SECRETS=true"
            )
            raise RuntimeError(
                f"Insecure startup rejected ({context}): jwt_secret_key must not be the default "
                "value 'change-me-in-production'. "
                "Set the JWT_SECRET_KEY environment variable to a cryptographically strong "
                "random string (e.g. `openssl rand -hex 32`). "
                "To allow the default secret in a non-production environment, set "
                "DEVNEST_ENV=development and DEVNEST_REQUIRE_SECRETS=false."
            )
        return self

    @model_validator(mode="after")
    def _validate_redis_config(self) -> Self:
        """Abort when distributed rate limiting is required but Redis URL is missing."""
        if self.devnest_require_distributed_rate_limiting and self.devnest_rate_limit_backend == "redis":
            if not (self.devnest_redis_url or "").strip():
                raise RuntimeError(
                    "DEVNEST_REQUIRE_DISTRIBUTED_RATE_LIMITING=true and "
                    "DEVNEST_RATE_LIMIT_BACKEND=redis, but DEVNEST_REDIS_URL is empty. "
                    "Set DEVNEST_REDIS_URL to a valid Redis connection URL."
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

    @model_validator(mode="after")
    def _validate_staging_production_placement_and_probes(self) -> Self:
        env = str(self.devnest_env or "development").strip().lower()
        if env not in ("production", "staging"):
            return self
        if self.devnest_allow_runtime_env_fallback:
            raise RuntimeError(
                "DEVNEST_ALLOW_RUNTIME_ENV_FALLBACK must be false in staging/production "
                "(use authoritative WorkspaceRuntime placement on EC2/VM nodes).",
            )
        if not self.devnest_require_ide_http_probe:
            raise RuntimeError(
                "DEVNEST_REQUIRE_IDE_HTTP_PROBE must be true in staging/production so RUNNING "
                "implies code-server HTTP readiness.",
            )
        if not self.devnest_workspace_http_probe_enabled:
            raise RuntimeError(
                "DEVNEST_WORKSPACE_HTTP_PROBE_ENABLED must be true in staging/production "
                "when IDE HTTP readiness is required.",
            )
        return self

    @model_validator(mode="after")
    def _validate_staging_production_reconcile_locking(self) -> Self:
        env = str(self.devnest_env or "development").strip().lower()
        if env not in ("production", "staging"):
            return self
        if not self.devnest_require_prod_reconcile_locking:
            return self
        url = (self.database_url or "").strip().lower()
        if not url.startswith("postgresql"):
            raise RuntimeError(
                "Staging/production with DEVNEST_REQUIRE_PROD_RECONCILE_LOCKING=true requires "
                "PostgreSQL (DATABASE_URL) so per-workspace reconcile advisory locks work.",
            )
        if self.devnest_reconcile_lock_backend != "postgres_advisory":
            raise RuntimeError(
                f"DEVNEST_RECONCILE_LOCK_BACKEND={self.devnest_reconcile_lock_backend!r} is not "
                "allowed in staging/production; use postgres_advisory.",
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
