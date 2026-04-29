"""Dataclasses for EC2 provisioning requests (explicit V1 configuration)."""

from __future__ import annotations

import base64
import binascii
import json
import shlex
from dataclasses import dataclass, field

from app.libs.common.config import Settings, get_settings

from .errors import Ec2ProvisionConfigurationError


@dataclass
class Ec2ProvisionRequest:
    """Inputs for ``run_instances`` — prefer explicit fields over implicit defaults in production."""

    ami_id: str
    instance_type: str
    subnet_id: str
    security_group_ids: list[str]
    iam_instance_profile_name: str | None = None
    key_name: str | None = None
    region: str | None = None
    node_key: str | None = None
    name_tag: str | None = None
    execution_mode: str | None = None
    ssh_user: str | None = None
    user_data: str | None = None
    extra_tags: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not (self.ami_id or "").strip():
            raise Ec2ProvisionConfigurationError("ami_id is required for EC2 provisioning")
        if not (self.subnet_id or "").strip():
            raise Ec2ProvisionConfigurationError("subnet_id is required for EC2 provisioning")
        if not self.security_group_ids:
            raise Ec2ProvisionConfigurationError(
                "security_group_ids must contain at least one security group for VPC instances",
            )
        if not (self.instance_type or "").strip():
            raise Ec2ProvisionConfigurationError("instance_type is required for EC2 provisioning")

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Ec2ProvisionRequest:
        """Build a request from ``DEVNEST_EC2_*`` settings (may still fail :meth:`validate`)."""
        s = settings or get_settings()
        raw_sg = (s.devnest_ec2_security_group_ids or "").strip()
        sg_ids = [x.strip() for x in raw_sg.split(",") if x.strip()]
        prof = (s.devnest_ec2_instance_profile or "").strip() or None
        key = (s.devnest_ec2_key_name or "").strip() or None
        region = (s.aws_region or "").strip() or None
        return cls(
            ami_id=(s.devnest_ec2_ami_id or "").strip(),
            instance_type=(s.devnest_ec2_instance_type or "").strip() or "t3.medium",
            subnet_id=(s.devnest_ec2_subnet_id or "").strip(),
            security_group_ids=sg_ids,
            iam_instance_profile_name=prof,
            key_name=key,
            region=region,
            user_data=_user_data_from_settings(s),
            extra_tags=_extra_tags_from_settings(s),
        )


def build_default_amazon_linux_2023_user_data(
    *,
    node_key: str,
    internal_api_base_url: str,
    internal_api_key: str,
    workspace_projects_base: str,
    heartbeat_interval_seconds: int,
) -> str:
    """Generate user-data that installs Docker and starts the DevNest heartbeat loop."""
    key = (node_key or "").strip()
    base = (internal_api_base_url or "").strip().rstrip("/")
    secret = internal_api_key or ""
    projects = (workspace_projects_base or "").strip() or "/var/lib/devnest/workspace-projects"
    interval = max(5, min(int(heartbeat_interval_seconds or 30), 3600))
    if not key:
        raise Ec2ProvisionConfigurationError("node_key is required to render EC2 bootstrap user-data")
    if not base:
        raise Ec2ProvisionConfigurationError(
            "DEVNEST_EC2_HEARTBEAT_INTERNAL_API_BASE_URL or INTERNAL_API_BASE_URL is required "
            "to render EC2 bootstrap user-data",
        )
    if not secret:
        raise Ec2ProvisionConfigurationError(
            "INTERNAL_API_KEY_INFRASTRUCTURE or INTERNAL_API_KEY is required to render EC2 bootstrap user-data",
        )

    q_node = shlex.quote(key)
    q_base = shlex.quote(base)
    q_secret = shlex.quote(secret)
    q_projects = shlex.quote(projects)
    service_name = "devnest-node-heartbeat"
    return f"""#!/bin/bash
set -Eeuo pipefail

install -d -m 0755 /var/log/devnest
exec > >(tee -a /var/log/devnest/bootstrap.log) 2>&1

dnf install -y docker awscli-2
systemctl enable --now docker

install -d -m 0755 /opt/devnest
install -d -m 0755 /var/lib/devnest
install -d -m 0775 {q_projects}

cat >/opt/devnest/heartbeat.env <<'ENV'
NODE_KEY={q_node}
INTERNAL_API_BASE_URL={q_base}
INTERNAL_API_KEY={q_secret}
HEARTBEAT_INTERVAL_SECONDS={interval}
ENV
chmod 0600 /opt/devnest/heartbeat.env

cat >/opt/devnest/node-heartbeat.sh <<'SCRIPT'
#!/bin/bash
set -Eeuo pipefail
source /opt/devnest/heartbeat.env
while true; do
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    DOCKER_OK=true
  else
    DOCKER_OK=false
  fi
  DISK_FREE_MB="$(df -Pm /var/lib/devnest 2>/dev/null | awk 'NR==2 {{print $4}}')"
  DISK_FREE_MB="${{DISK_FREE_MB:-0}}"
  PAYLOAD="$(printf '{{"node_key":"%s","docker_ok":%s,"disk_free_mb":%s,"version":"ec2-user-data-v1"}}' \\
    "${{NODE_KEY}}" "${{DOCKER_OK}}" "${{DISK_FREE_MB}}")"
  curl -fsS -X POST "${{INTERNAL_API_BASE_URL%/}}/internal/execution-nodes/heartbeat" \\
    -H "Content-Type: application/json" \\
    -H "X-Internal-API-Key: ${{INTERNAL_API_KEY}}" \\
    --data "${{PAYLOAD}}" \\
    >/dev/null || true
  sleep "${{HEARTBEAT_INTERVAL_SECONDS:-30}}"
done
SCRIPT
chmod 0755 /opt/devnest/node-heartbeat.sh

cat >/etc/systemd/system/{service_name}.service <<'UNIT'
[Unit]
Description=DevNest execution node heartbeat
After=network-online.target docker.service
Wants=network-online.target docker.service

[Service]
Type=simple
EnvironmentFile=/opt/devnest/heartbeat.env
ExecStart=/opt/devnest/node-heartbeat.sh
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now {service_name}.service

# Values quoted for shellcheck/readability; heartbeat.env is authoritative.
printf 'DevNest bootstrap complete for node %s using API %s and projects base %s\\n' {q_node} {q_base} {q_projects}
"""


def _user_data_from_settings(settings: Settings) -> str | None:
    raw_b64 = (getattr(settings, "devnest_ec2_user_data_b64", "") or "").strip()
    raw = getattr(settings, "devnest_ec2_user_data", "") or ""
    if raw_b64:
        try:
            return base64.b64decode(raw_b64, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as e:
            raise Ec2ProvisionConfigurationError("DEVNEST_EC2_USER_DATA_B64 must be valid UTF-8 base64") from e
    return raw if raw.strip() else None


def _extra_tags_from_settings(settings: Settings) -> dict[str, str]:
    raw = (getattr(settings, "devnest_ec2_extra_tags", "") or "").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise Ec2ProvisionConfigurationError("DEVNEST_EC2_EXTRA_TAGS must be JSON or comma key=value pairs") from e
        if not isinstance(data, dict):
            raise Ec2ProvisionConfigurationError("DEVNEST_EC2_EXTRA_TAGS JSON must be an object")
        return {str(k).strip(): str(v).strip() for k, v in data.items() if str(k).strip() and str(v).strip()}
    out: dict[str, str] = {}
    for part in raw.replace(";", ",").split(","):
        item = part.strip()
        if not item:
            continue
        if "=" not in item:
            raise Ec2ProvisionConfigurationError("DEVNEST_EC2_EXTRA_TAGS entries must use key=value")
        k, v = item.split("=", 1)
        if k.strip() and v.strip():
            out[k.strip()] = v.strip()
    return out
