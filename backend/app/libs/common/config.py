import os
from functools import lru_cache

from pydantic import field_validator
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
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14
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

    # Service-to-service: required for POST /internal/notifications (header X-Internal-API-Key).
    internal_api_key: str = ""

    # Workspace orchestrator (Docker): image for workspace containers; empty falls back to env then nginx:alpine.
    workspace_container_image: str = ""
    # Host directory root for per-workspace project bind mounts; empty uses system temp / devnest-workspaces.
    workspace_projects_base: str = ""

    # Standalone gateway route-admin (data plane): register/deregister workspace routes after orchestration.
    # DEVNEST_GATEWAY_URL is the route-admin HTTP base (not Traefik's public :80). Default matches compose
    # DEVNEST_ROUTE_ADMIN_PORT=9080.
    devnest_gateway_url: str = "http://127.0.0.1:9080"
    devnest_base_domain: str = "app.devnest.local"
    devnest_gateway_enabled: bool = False
    # Used for gateway_url hint on attach/access when route registration is enabled (no TLS in V1).
    devnest_gateway_public_scheme: str = "http"

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

    @field_validator("devnest_gateway_enabled", mode="before")
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

    # Autoscaler (V1): fleet-level EC2 capacity; off by default for safe local/dev behavior.
    devnest_autoscaler_enabled: bool = False
    # When set with ``devnest_autoscaler_enabled``, worker triggers one EC2 provision on NoSchedulableNodeError.
    devnest_autoscaler_provision_on_no_capacity: bool = False
    devnest_autoscaler_max_concurrent_provisioning: int = 3
    # Do not reclaim EC2 nodes unless at least this many READY+schedulable EC2 nodes exist (last-node safety).
    devnest_autoscaler_min_ec2_nodes_before_reclaim: int = 2

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
        return max(1, min(n, 100))


@lru_cache
def get_settings() -> Settings:
    return Settings()
