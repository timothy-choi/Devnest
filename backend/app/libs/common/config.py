import contextvars
import os
import shlex
from functools import lru_cache
from pathlib import Path
from typing import Self
from urllib.parse import quote_plus, urlencode, urlparse

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def format_database_url_for_log(dsn: str) -> str:
    """Return driver/host/port/database for logs — never username or password."""
    raw = (dsn or "").strip()
    if not raw:
        return "database=<empty>"
    try:
        from sqlalchemy.engine.url import make_url

        u = make_url(raw)
        return (
            f"driver={u.drivername or 'unknown'} "
            f"host={u.host or '<none>'} "
            f"port={u.port or ''} "
            f"database={u.database or '<none>'}"
        )
    except Exception:
        return "database=<unparseable>"


def database_host_and_name_for_log(dsn: str) -> tuple[str, str]:
    """Safe (host, database name) tuple for diagnostics — no credentials."""
    raw = (dsn or "").strip()
    if not raw:
        return ("<empty>", "<empty>")
    try:
        from sqlalchemy.engine.url import make_url

        u = make_url(raw)
        return (u.host or "<none>", u.database or "<none>")
    except Exception:
        return ("<unparseable>", "<unparseable>")


def _normalized_public_base_for_log(raw: str) -> str:
    value = (raw or "").strip().rstrip("/")
    if not value:
        return ""
    if "://" not in value:
        value = f"http://{value}"
    return value


def _hostname_from_public_base(raw: str) -> str:
    value = _normalized_public_base_for_log(raw)
    if not value:
        return ""
    try:
        return (urlparse(value).hostname or "").lower()
    except ValueError:
        return ""


def is_loopback_public_base(raw: str) -> bool:
    return _hostname_from_public_base(raw) in ("", "localhost", "127.0.0.1", "::1")


def oauth_startup_status_for_log(settings: "Settings") -> dict[str, object]:
    github_base = _normalized_public_base_for_log(settings.github_oauth_public_base_url)
    google_base = _normalized_public_base_for_log(settings.gcloud_oauth_public_base_url)
    frontend_base = _normalized_public_base_for_log(settings.devnest_frontend_public_base_url)
    github_configured = bool(
        (settings.oauth_github_client_id or "").strip()
        and (settings.oauth_github_client_secret or "").strip()
        and github_base
    )
    google_configured = bool(
        (settings.oauth_google_client_id or "").strip()
        and (settings.oauth_google_client_secret or "").strip()
        and google_base
    )
    return {
        "frontend_public_base_url": frontend_base or "-",
        "github_oauth_public_base_url": github_base or "-",
        "gcloud_oauth_public_base_url": google_base or "-",
        "github_oauth_configured": github_configured,
        "google_oauth_configured": google_configured,
    }


# Database URL resolution (application + Alembic via get_settings().database_url):
#   1. os.environ["DEVNEST_DATABASE_URL"] — highest precedence (explicit DevNest name).
#   2. os.environ["DATABASE_URL"] — standard; must win over repo ``backend/.env`` file fallbacks.
#   3. ``backend/.env`` / cwd ``.env`` file: DEVNEST_DATABASE_URL, then DATABASE_URL (see _repo_env_fallbacks).
#   4. Pydantic field / component env (postgres_* construction in _derive_database_url).
# Docker Compose: ``services.*.environment`` injects keys into the container process env, so (1)-(2) pick up
# the compose file values and **override the same keys** from ``env_file: backend/.env`` — backend and worker
# therefore cannot silently fall back to a different DSN from ``backend/.env`` when compose sets DATABASE_URL.
# Alembic ``env.py`` uses only ``get_settings().database_url`` so migrations and the API always agree.
#
# Optional integration guards (fail fast when mis-set): ``DEVNEST_EXPECT_EXTERNAL_POSTGRES``,
# ``DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS`` (see ``_validate_integration_startup_expectations``).

# When true, ``Settings(database_url=...)`` was called with that keyword — do not replace it from
# ``os.environ`` / repo ``.env`` inside ``_prefer_devnest_database_url_alias`` (matches pydantic-settings:
# init kwargs override environment for tests and one-off tools).
_explicit_database_url_from_init_kwarg: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_explicit_database_url_from_init_kwarg",
    default=False,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="",
        validation_alias=AliasChoices("DEVNEST_DATABASE_URL", "database_url", "DATABASE_URL"),
    )
    postgres_host: str = ""
    postgres_port: int = 5432
    postgres_db: str = ""
    postgres_user: str = ""
    postgres_password: str = ""
    postgres_sslmode: str = ""
    postgres_sslrootcert: str = ""
    devnest_db_auto_create: bool = True
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
    # When true, exposes GET /internal/devnest-auth-diagnostics (JSON only; no secrets). Disable in prod.
    devnest_auth_diagnostics_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("DEVNEST_AUTH_DIAGNOSTICS", "devnest_auth_diagnostics_enabled"),
    )
    # When true, reject resolved DB host ``postgres`` (bundled compose service) so RDS deploys do not
    # silently use local Postgres. ``scripts/deploy-ec2.sh`` sets this when ``DATABASE_URL`` is set.
    devnest_expect_external_postgres: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "DEVNEST_EXPECT_EXTERNAL_POSTGRES",
            "devnest_expect_external_postgres",
        ),
    )
    # When true, reject ``app.lvh.me`` / ``app.devnest.local`` as DEVNEST_BASE_DOMAIN (client-side / lab DNS).
    devnest_expect_remote_gateway_clients: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS",
            "devnest_expect_remote_gateway_clients",
        ),
    )

    # OAuth redirect bases (no trailing slash). Env may be host:port only; code prepends http:// if needed.
    github_oauth_public_base_url: str = ""
    gcloud_oauth_public_base_url: str = ""
    oauth_github_client_id: str = ""
    oauth_github_client_secret: str = ""
    oauth_google_client_id: str = ""
    oauth_google_client_secret: str = ""
    # Browser-visible UI origin (``docker-compose.integration.yml`` / EC2). Used to fix OAuth redirect
    # bases when ``backend/.env`` still pins ``GITHUB_OAUTH_PUBLIC_BASE_URL`` to localhost.
    devnest_frontend_public_base_url: str = ""

    # --- Internal platform auth (sensitive; header X-Internal-API-Key) ---
    # Legacy single key: used for any internal surface whose scope-specific key is unset.
    # Prefer per-scope keys in production so workers, autoscaler, infra automation, etc. do not share one secret.
    internal_api_key: str = ""
    internal_api_key_workspace_jobs: str = ""
    internal_api_key_workspace_reconcile: str = ""
    internal_api_key_autoscaler: str = ""
    internal_api_key_infrastructure: str = ""
    internal_api_key_notifications: str = ""
    # Workers / sidecars: base URL for calling FastAPI internal routes (e.g. http://backend:8000).
    internal_api_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("INTERNAL_API_BASE_URL", "internal_api_base_url"),
    )
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
    # S3 provider settings (used when devnest_snapshot_storage_provider=s3).
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
    # Optional Traefik HTTP base (e.g. ``http://traefik:80`` on the Docker network). When set, attach/access
    # waits until a GET with ``Host: <workspace hostname>`` is not 404 so the edge has loaded dynamic routes.
    devnest_gateway_traefik_http_probe_base: str = ""

    # Outbound notification email (optional). If smtp_host is empty, the email channel stays in stub mode.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_address: str = ""
    smtp_use_tls: bool = True

    @staticmethod
    @lru_cache(maxsize=1)
    def _repo_env_fallbacks() -> dict[str, str]:
        candidates = (
            Path.cwd() / ".env",
            Path.cwd() / "backend" / ".env",
        )
        values: dict[str, str] = {}
        for path in candidates:
            if not path.exists():
                continue
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key:
                    continue
                values.setdefault(key, value.strip())
            if values:
                break
        return values

    @staticmethod
    def _coerce_libpq_database_url(raw: str) -> str:
        value = (raw or "").strip()
        if not value or "://" in value or "=" not in value:
            return value
        try:
            parts = shlex.split(value)
        except ValueError:
            return value

        parsed: dict[str, str] = {}
        for part in parts:
            if "=" not in part:
                return value
            key, part_value = part.split("=", 1)
            parsed[key.strip().lower()] = part_value.strip()

        host = parsed.get("host", "")
        db = parsed.get("dbname", "") or parsed.get("database", "")
        user = parsed.get("user", "")
        if not (host and db and user):
            return value

        password = parsed.get("password", "")
        port = parsed.get("port", "5432").strip() or "5432"
        userinfo = quote_plus(user)
        if password:
            userinfo = f"{userinfo}:{quote_plus(password)}"

        query_params = {
            "sslmode": parsed.get("sslmode", "").strip(),
            "sslrootcert": parsed.get("sslrootcert", "").strip(),
        }
        query = urlencode({k: v for k, v in query_params.items() if v})
        url = f"postgresql+psycopg://{userinfo}@{host}:{port}/{db}"
        return f"{url}?{query}" if query else url

    @field_validator(
        "github_oauth_public_base_url",
        "gcloud_oauth_public_base_url",
        "oauth_github_client_id",
        "oauth_github_client_secret",
        "oauth_google_client_id",
        "oauth_google_client_secret",
        mode="before",
    )
    @classmethod
    def _oauth_env_aliases(cls, v, info):  # noqa: ANN001
        if isinstance(v, str) and v.strip():
            return v

        alias_map = {
            "github_oauth_public_base_url": ("GITHUB_OAUTH_PUBLIC_BASE_URL",),
            "gcloud_oauth_public_base_url": ("GCLOUD_OAUTH_PUBLIC_BASE_URL",),
            "oauth_github_client_id": ("OAUTH_GITHUB_CLIENT_ID", "GITHUB_CLIENT_ID", "GITHUB_OAUTH_CLIENT_ID"),
            "oauth_github_client_secret": (
                "OAUTH_GITHUB_CLIENT_SECRET",
                "GITHUB_CLIENT_SECRET",
                "GITHUB_OAUTH_CLIENT_SECRET",
            ),
            "oauth_google_client_id": ("OAUTH_GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_ID"),
            "oauth_google_client_secret": (
                "OAUTH_GOOGLE_CLIENT_SECRET",
                "GOOGLE_CLIENT_SECRET",
                "GOOGLE_OAUTH_CLIENT_SECRET",
            ),
        }
        for env_name in alias_map.get(info.field_name, ()):
            raw = os.getenv(env_name, "")
            if raw.strip():
                return raw

        for env_name in alias_map.get(info.field_name, ()):
            raw = cls._repo_env_fallbacks().get(env_name, "")
            if raw.strip():
                return raw
        return v

    @field_validator("smtp_use_tls", mode="before")
    @classmethod
    def _parse_smtp_use_tls(cls, v):  # noqa: ANN001 — pydantic coerces env strings
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    @field_validator("database_url", mode="before")
    @classmethod
    def _database_url_aliases(cls, v):  # noqa: ANN001
        if isinstance(v, str) and v.strip():
            return cls._coerce_libpq_database_url(v.strip())
        for env_name in ("DEVNEST_DATABASE_URL", "database_url", "DATABASE_URL"):
            raw = os.getenv(env_name, "")
            if raw.strip():
                return cls._coerce_libpq_database_url(raw.strip())
        for env_name in ("DEVNEST_DATABASE_URL", "database_url", "DATABASE_URL"):
            raw = cls._repo_env_fallbacks().get(env_name, "")
            if raw.strip():
                return cls._coerce_libpq_database_url(raw.strip())
        return v

    @field_validator("postgres_port", mode="before")
    @classmethod
    def _coerce_postgres_port(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 5432
        return max(1, min(n, 65535))

    @field_validator(
        "devnest_expect_external_postgres",
        "devnest_expect_remote_gateway_clients",
        mode="before",
    )
    @classmethod
    def _parse_expect_startup_flags(cls, v):  # noqa: ANN001
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("", "0", "false", "no", "off"):
                return False
            if s in ("1", "true", "yes", "on"):
                return True
            return False
        return bool(v)

    @field_validator(
        "devnest_gateway_enabled",
        "devnest_gateway_auth_enabled",
        "devnest_workspace_projects_prune_orphans_on_startup",
        "devnest_db_auto_create",
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

    @model_validator(mode="before")
    @classmethod
    def _mark_explicit_database_url_kwarg(cls, data: object) -> object:
        if isinstance(data, dict):
            _explicit_database_url_from_init_kwarg.set("database_url" in data)
        else:
            _explicit_database_url_from_init_kwarg.set(False)
        return data

    @model_validator(mode="after")
    def _derive_database_url(self) -> Self:
        if (self.database_url or "").strip():
            self.database_url = self.database_url.strip()
            return self

        host = str(self.postgres_host or "").strip()
        db = str(self.postgres_db or "").strip()
        user = str(self.postgres_user or "").strip()
        if not (host and db and user):
            return self

        query: dict[str, str] = {}
        sslmode = str(self.postgres_sslmode or "").strip()
        sslrootcert = str(self.postgres_sslrootcert or "").strip()
        if sslmode:
            query["sslmode"] = sslmode
        if sslrootcert:
            query["sslrootcert"] = sslrootcert
        suffix = f"?{urlencode(query)}" if query else ""
        self.database_url = (
            "postgresql+psycopg://"
            f"{quote_plus(user)}:{quote_plus(str(self.postgres_password or ''))}"
            f"@{host}:{int(self.postgres_port)}/{db}{suffix}"
        )
        return self

    @model_validator(mode="after")
    def _prefer_devnest_database_url_alias(self) -> Self:
        """Single authoritative merge: OS env beats repo ``.env`` files; DEVNEST_DATABASE_URL beats DATABASE_URL."""
        try:
            if _explicit_database_url_from_init_kwarg.get():
                self.database_url = self._coerce_libpq_database_url(str(self.database_url or "").strip())
                return self

            raw = os.getenv("DEVNEST_DATABASE_URL", "").strip()
            if raw:
                self.database_url = self._coerce_libpq_database_url(raw)
                return self
            raw = os.getenv("DATABASE_URL", "").strip()
            if raw:
                self.database_url = self._coerce_libpq_database_url(raw)
                return self
            raw = (self._repo_env_fallbacks().get("DEVNEST_DATABASE_URL") or "").strip()
            if raw:
                self.database_url = self._coerce_libpq_database_url(raw)
                return self
            raw = (self._repo_env_fallbacks().get("DATABASE_URL") or "").strip()
            if raw:
                self.database_url = self._coerce_libpq_database_url(raw)
                return self

            self.database_url = self._coerce_libpq_database_url(str(self.database_url or "").strip())

            return self
        finally:
            _explicit_database_url_from_init_kwarg.set(False)

    @staticmethod
    def _hostname_is_loopback(host: str) -> bool:
        h = (host or "").strip().lower()
        return h in ("localhost", "127.0.0.1", "::1", "")

    @model_validator(mode="after")
    def _sync_oauth_public_bases_from_devnest_frontend(self) -> Self:
        """Prefer ``DEVNEST_FRONTEND_PUBLIC_BASE_URL`` when OAuth bases are loopback (common ``backend/.env``)."""
        front = (self.devnest_frontend_public_base_url or "").strip().rstrip("/")
        if not front:
            raw = os.getenv("DEVNEST_FRONTEND_PUBLIC_BASE_URL", "").strip().rstrip("/")
            front = raw
        if not front:
            return self
        try:
            parsed = urlparse(front if "://" in front else f"http://{front}")
            fh = (parsed.hostname or "").lower()
        except ValueError:
            return self
        if self._hostname_is_loopback(fh):
            return self

        for attr in ("github_oauth_public_base_url", "gcloud_oauth_public_base_url"):
            current = (getattr(self, attr) or "").strip()
            if not current:
                setattr(self, attr, front)
                continue
            try:
                cur_p = urlparse(current if "://" in current else f"http://{current}")
                ch = (cur_p.hostname or "").lower()
            except ValueError:
                continue
            if self._hostname_is_loopback(ch):
                setattr(self, attr, front)
        return self

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

    # AWS (EC2 node registry, S3 snapshots; optional — uses default credential chain when keys empty).
    aws_region: str = Field(
        default="",
        validation_alias=AliasChoices("AWS_REGION", "aws_region"),
        description="Region for AWS APIs (S3 snapshots, EC2 helpers). Required when snapshot provider is s3.",
    )
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    # Placement filter: ``all`` (default) = local + EC2 nodes; ``local`` / ``ec2`` = restrict pool.
    devnest_node_provider: str = "all"
    # When false, new placement is restricted to the primary node only: lowest ``execution_node.id``
    # among READY+schedulable rows after the provider filter. When true (recommended for multi-node
    # fleets; integration Compose defaults this on), all READY+schedulable nodes in the provider pool
    # compete using capacity + spread ordering (and optional heartbeat freshness when
    # DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT is set).
    # See docs/PHASE_3B_STEP7_MULTI_NODE_SCHEDULING_FLAG.md and docs/PHASE_3B_STEP11_TWO_NODE_SCHEDULING_SPREAD.md.
    devnest_enable_multi_node_scheduling: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "DEVNEST_ENABLE_MULTI_NODE_SCHEDULING",
            "devnest_enable_multi_node_scheduling",
        ),
    )
    # Phase 3b Step 8: internal pinned CREATE for allowlisted execution_node.id (operator test workspace).
    devnest_allow_pinned_create_placement: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "DEVNEST_ALLOW_PINNED_CREATE_PLACEMENT",
            "devnest_allow_pinned_create_placement",
        ),
    )
    # Comma-separated ``execution_node.id`` values permitted for pinned operator CREATE.
    devnest_pinned_create_execution_node_ids: str = Field(
        default="",
        validation_alias=AliasChoices(
            "DEVNEST_PINNED_CREATE_EXECUTION_NODE_IDS",
            "devnest_pinned_create_execution_node_ids",
        ),
    )
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
    # Optional comma-separated tags for autoscaled EC2 nodes: ``key=value,owner=devnest``.
    devnest_ec2_extra_tags: str = ""
    # Optional EC2 user-data bootstrap. Prefer ``DEVNEST_EC2_USER_DATA_B64`` for multi-line cloud-init.
    devnest_ec2_user_data: str = ""
    devnest_ec2_user_data_b64: str = ""
    # Set true only when the AMI already installs Docker and starts the DevNest heartbeat agent.
    devnest_ec2_bootstrap_prebaked: bool = False

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
    # Phase 3a: workspace-worker (or in-process worker tick) writes execution_node.last_heartbeat_at.
    devnest_worker_emit_execution_node_heartbeat: bool = True
    devnest_execution_node_heartbeat_emitter_version: str = "worker-embedded"
    # When true, new placement skips nodes whose last_heartbeat_at is NULL or older than max age.
    devnest_require_fresh_node_heartbeat: bool = False
    devnest_node_heartbeat_max_age_seconds: int = 300
    # Dedicated workspace-worker heartbeat loop: POST /internal/execution-nodes/heartbeat on an interval.
    devnest_node_heartbeat_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("DEVNEST_NODE_HEARTBEAT_ENABLED", "devnest_node_heartbeat_enabled"),
    )
    # Heartbeat ``node_key`` when the dedicated emitter is enabled; empty uses default local node (e.g. node-1).
    devnest_node_key: str = Field(
        default="",
        validation_alias=AliasChoices("DEVNEST_NODE_KEY", "devnest_node_key"),
    )
    devnest_node_heartbeat_interval_seconds: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "DEVNEST_NODE_HEARTBEAT_INTERVAL_SECONDS",
            "devnest_node_heartbeat_interval_seconds",
        ),
    )
    # When set (e.g. http://backend:8000 in Docker Compose), workspace-worker POSTs heartbeats to
    # ``{base}/internal/execution-nodes/heartbeat`` so ``last_heartbeat_at`` is written via the API
    # (same INTERNAL_API_KEY / infrastructure scope as other internal routes). Empty = direct DB write.
    devnest_worker_heartbeat_internal_api_base_url: str = ""

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

    # Autoscaler (V1/V2): fleet-level EC2 capacity. Phase 2 allows safe scale-out only by default.
    devnest_autoscaler_enabled: bool = True
    # When true, log fleet decisions without provisioning, draining, or terminating.
    devnest_autoscaler_evaluate_only: bool = False
    devnest_autoscaler_min_nodes: int = 1
    devnest_autoscaler_max_nodes: int = 10
    devnest_autoscaler_min_idle_slots: int = 1
    # When set with ``devnest_autoscaler_enabled``, worker triggers one EC2 provision on NoSchedulableNodeError.
    devnest_autoscaler_provision_on_no_capacity: bool = False
    devnest_autoscaler_max_concurrent_provisioning: int = 3
    devnest_autoscaler_scale_out_cooldown_seconds: int = 300
    devnest_autoscaler_scale_in_cooldown_seconds: int = 900
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

    @field_validator("devnest_enable_multi_node_scheduling", mode="before")
    @classmethod
    def _parse_devnest_enable_multi_node_scheduling(cls, v):  # noqa: ANN001
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("1", "true", "yes", "on"):
                return True
            if s in ("0", "false", "no", "off", ""):
                return False
        return bool(v)

    @field_validator("devnest_allow_pinned_create_placement", mode="before")
    @classmethod
    def _parse_devnest_allow_pinned_create_placement(cls, v):  # noqa: ANN001
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("1", "true", "yes", "on"):
                return True
            if s in ("0", "false", "no", "off", ""):
                return False
        return bool(v)

    @field_validator(
        "devnest_worker_emit_execution_node_heartbeat",
        "devnest_require_fresh_node_heartbeat",
        "devnest_node_heartbeat_enabled",
        mode="before",
    )
    @classmethod
    def _parse_execution_node_heartbeat_flags(cls, v):  # noqa: ANN001
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    @field_validator("devnest_node_heartbeat_interval_seconds", mode="before")
    @classmethod
    def _coerce_node_heartbeat_interval_seconds(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 30
        return max(5, min(n, 3600))

    @field_validator("devnest_node_heartbeat_max_age_seconds", mode="before")
    @classmethod
    def _coerce_node_heartbeat_max_age_seconds(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 300
        return max(30, min(n, 86_400))

    @field_validator("devnest_node_key", mode="before")
    @classmethod
    def _strip_devnest_node_key(cls, v):  # noqa: ANN001
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("internal_api_base_url", mode="before")
    @classmethod
    def _strip_internal_api_base_url(cls, v):  # noqa: ANN001
        if v is None:
            return ""
        return str(v).strip().rstrip("/")

    @field_validator("devnest_worker_heartbeat_internal_api_base_url", mode="before")
    @classmethod
    def _strip_worker_heartbeat_internal_api_base_url(cls, v):  # noqa: ANN001
        if v is None:
            return ""
        return str(v).strip().rstrip("/")

    @field_validator(
        "devnest_autoscaler_enabled",
        "devnest_autoscaler_evaluate_only",
        "devnest_autoscaler_provision_on_no_capacity",
        "devnest_ec2_bootstrap_prebaked",
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

    @field_validator("devnest_autoscaler_min_nodes", mode="before")
    @classmethod
    def _autoscaler_min_nodes(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 1
        return max(0, min(n, 100))

    @field_validator("devnest_autoscaler_max_nodes", mode="before")
    @classmethod
    def _autoscaler_max_nodes(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 10
        return max(1, min(n, 500))

    @field_validator("devnest_autoscaler_min_idle_slots", mode="before")
    @classmethod
    def _autoscaler_min_idle_slots(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 1
        return max(0, min(n, 10_000))

    @field_validator(
        "devnest_autoscaler_scale_out_cooldown_seconds",
        "devnest_autoscaler_scale_in_cooldown_seconds",
        mode="before",
    )
    @classmethod
    def _autoscaler_cooldown_seconds(cls, v):  # noqa: ANN001
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 300
        return max(0, min(n, 86_400))

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

    @model_validator(mode="after")
    def _validate_resolved_database_url(self) -> Self:
        """Reject malformed DSNs; require psycopg URL form when cloud/RDS posture flags are on."""
        url = (self.database_url or "").strip()
        if not url:
            return self
        if "://" not in url and "=" in url and ("host=" in url.lower() or "dbname=" in url.lower()):
            raise RuntimeError(
                "DATABASE_URL / DEVNEST_DATABASE_URL looks like a libpq keyword DSN but was not converted "
                "to a URL (missing host, dbname, or user?). Use a single-line "
                "postgresql+psycopg://USER:PASSWORD@HOST:PORT/DBNAME?... URL, or a complete libpq keyword string."
            )
        try:
            from sqlalchemy.engine.url import make_url

            u = make_url(url)
        except Exception as exc:
            raise RuntimeError(
                "Malformed database connection URL (DATABASE_URL / DEVNEST_DATABASE_URL): "
                f"could not parse as SQLAlchemy URL ({type(exc).__name__}: {exc}). "
                "Use a single-line postgresql+psycopg://USER:PASSWORD@HOST:PORT/DBNAME?... value."
            ) from exc
        driver = (u.drivername or "").lower()
        # SQLite and other non-Postgres URLs are allowed for unit tests and local tools; only
        # enforce host/database/psycopg rules for PostgreSQL family URLs.
        if not driver.startswith("postgresql"):
            return self

        cloud = self.devnest_expect_external_postgres or self.devnest_expect_remote_gateway_clients
        if cloud and (u.drivername or "") != "postgresql+psycopg":
            raise RuntimeError(
                "RDS / remote integration posture requires SQLAlchemy psycopg v3 URLs "
                f"(postgresql+psycopg://…); got driver {u.drivername!r}. "
                "Fix DATABASE_URL / DEVNEST_DATABASE_URL or generate .env.integration via "
                "scripts/write_integration_deploy_env.py write."
            )
        dbn = (u.database or "").strip() if u.database is not None else ""
        if not dbn:
            raise RuntimeError(
                "PostgreSQL DATABASE_URL must include a database name in the path "
                "(postgresql+psycopg://…@HOST:PORT/DBNAME)."
            )
        if not (u.host or "").strip():
            raise RuntimeError(
                "PostgreSQL DATABASE_URL must include a non-empty database host "
                "(postgresql+psycopg://…@HOST:PORT/DBNAME)."
            )
        return self

    @model_validator(mode="after")
    def _validate_integration_startup_expectations(self) -> Self:
        """Fail fast when EC2/RDS-style expectations contradict bundled or lab-only defaults."""
        if self.devnest_expect_external_postgres:
            host = ""
            try:
                from sqlalchemy.engine.url import make_url

                u = make_url(self.database_url)
                host = (u.host or "").lower()
            except Exception:
                host = ""
            if host == "postgres":
                raise RuntimeError(
                    "DEVNEST_EXPECT_EXTERNAL_POSTGRES=true but the resolved database host is "
                    "'postgres' (bundled docker-compose Postgres). Set DEVNEST_COMPOSE_DATABASE_URL "
                    "or DATABASE_URL to your managed Postgres/RDS DSN, or set "
                    "DEVNEST_EXPECT_EXTERNAL_POSTGRES=false when using the bundled postgres service."
                )
        if self.devnest_expect_remote_gateway_clients:
            domain = (self.devnest_base_domain or "").strip().lower()
            if not domain:
                raise RuntimeError(
                    "DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS=true but DEVNEST_BASE_DOMAIN is empty. "
                    "Set DEVNEST_BASE_DOMAIN to a hostname remote browsers resolve for ws-<id>.<domain>, "
                    "or rely on deploy-ec2.sh to derive <public-ip>.sslip.io on EC2."
                )
            if domain in ("app.lvh.me", "app.devnest.local"):
                raise RuntimeError(
                    "DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS=true but DEVNEST_BASE_DOMAIN is set to a "
                    f"hostname that does not resolve for remote browsers ({domain!r}). Use sslip.io, "
                    "a wildcard DNS zone pointing at this host, or similar; see docs/INTEGRATION_STARTUP.md."
                )
            gh_id = (self.oauth_github_client_id or "").strip()
            gh_sec = (self.oauth_github_client_secret or "").strip()
            if bool(gh_id) ^ bool(gh_sec):
                raise RuntimeError(
                    "Incomplete GitHub OAuth: set both OAUTH_GITHUB_CLIENT_ID and OAUTH_GITHUB_CLIENT_SECRET "
                    "(or legacy GH_/GITHUB_ aliases), or clear both to disable GitHub OAuth "
                    "(DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS=true)."
                )
            go_id = (self.oauth_google_client_id or "").strip()
            go_sec = (self.oauth_google_client_secret or "").strip()
            if bool(go_id) ^ bool(go_sec):
                raise RuntimeError(
                    "Incomplete Google OAuth: set both OAUTH_GOOGLE_CLIENT_ID and OAUTH_GOOGLE_CLIENT_SECRET "
                    "(or legacy GOOGLE_ aliases), or clear both to disable Google OAuth "
                    "(DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS=true)."
                )
            if is_loopback_public_base(self.devnest_frontend_public_base_url):
                raise RuntimeError(
                    "DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS=true but DEVNEST_FRONTEND_PUBLIC_BASE_URL is "
                    "empty or loopback-only. Set it to the browser-visible UI origin "
                    "(for example http://203-0-113-10.sslip.io:3000)."
                )
            github_creds_present = bool(
                (self.oauth_github_client_id or "").strip() and (self.oauth_github_client_secret or "").strip()
            )
            google_creds_present = bool(
                (self.oauth_google_client_id or "").strip() and (self.oauth_google_client_secret or "").strip()
            )
            if github_creds_present and is_loopback_public_base(self.github_oauth_public_base_url):
                raise RuntimeError(
                    "GitHub OAuth credentials are set, but GITHUB_OAUTH_PUBLIC_BASE_URL resolves to an "
                    "empty or loopback-only host. In EC2/remote mode set DEVNEST_FRONTEND_PUBLIC_BASE_URL "
                    "or GITHUB_OAUTH_PUBLIC_BASE_URL to the public UI origin."
                )
            if google_creds_present and is_loopback_public_base(self.gcloud_oauth_public_base_url):
                raise RuntimeError(
                    "Google OAuth credentials are set, but GCLOUD_OAUTH_PUBLIC_BASE_URL resolves to an "
                    "empty or loopback-only host. In EC2/remote mode set DEVNEST_FRONTEND_PUBLIC_BASE_URL "
                    "or GCLOUD_OAUTH_PUBLIC_BASE_URL to the public UI origin."
                )
        return self

    @model_validator(mode="after")
    def _validate_snapshot_storage_config(self) -> Self:
        provider = (self.devnest_snapshot_storage_provider or "local").strip().lower()
        if provider not in ("local", "s3"):
            raise RuntimeError(
                "DEVNEST_SNAPSHOT_STORAGE_PROVIDER must be 'local' or 's3' "
                f"(got {self.devnest_snapshot_storage_provider!r})."
            )
        cloud_posture = bool(
            self.devnest_expect_external_postgres or self.devnest_expect_remote_gateway_clients
        )
        if cloud_posture and provider != "s3":
            raise RuntimeError(
                "Integration/cloud posture is enabled (DEVNEST_EXPECT_EXTERNAL_POSTGRES and/or "
                "DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS) but DEVNEST_SNAPSHOT_STORAGE_PROVIDER is not 's3'. "
                "Snapshot archives must use object storage so the API and workspace-worker stay consistent "
                "across hosts. Set DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3, DEVNEST_S3_SNAPSHOT_BUCKET, AWS_REGION, "
                "and optional DEVNEST_S3_SNAPSHOT_PREFIX (see docs/INTEGRATION_STARTUP.md). "
                "Or set both expect flags to false for single-node/local snapshot storage only."
            )
        if provider != "s3":
            return self

        bucket = (self.devnest_s3_snapshot_bucket or "").strip()
        region = (self.aws_region or "").strip()
        missing: list[str] = []
        if not bucket:
            missing.append("DEVNEST_S3_SNAPSHOT_BUCKET")
        if not region:
            missing.append("AWS_REGION")
        if missing:
            raise RuntimeError(
                "S3 snapshot storage selected but required configuration is missing: "
                + ", ".join(missing)
                + ". Set DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3 only when the S3 snapshot bucket "
                "and AWS region are configured."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
