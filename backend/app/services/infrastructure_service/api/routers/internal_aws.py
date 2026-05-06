"""Internal AWS observability (orphaned DevNest-tagged resources)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.libs.common.config import get_settings
from app.libs.security.dependencies import require_internal_api_key
from app.libs.security.internal_auth import InternalApiScope
from app.services.infrastructure_service.ec2_cleanup import discover_devnest_autocleanup_orphans
from app.services.providers.ec2_provider import build_ec2_client

router = APIRouter(
    prefix="/internal/aws",
    tags=["internal-aws"],
    dependencies=[Depends(require_internal_api_key(InternalApiScope.INFRASTRUCTURE))],
)


@router.get("/orphans")
def list_devnest_aws_orphans() -> dict:
    """List DevNest autocleanup-tagged orphan candidates (no deletes)."""
    settings = get_settings()
    client = build_ec2_client()
    report = discover_devnest_autocleanup_orphans(client, settings)
    return {
        "orphaned_volumes": report.volumes,
        "orphaned_network_interfaces": report.network_interfaces,
        "orphaned_elastic_ips": report.elastic_ips,
        "orphaned_security_groups": report.security_groups,
    }
