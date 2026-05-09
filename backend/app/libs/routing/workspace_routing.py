"""Multi-tenant workspace URLs: ``https://<route_subdomain>.<public_base>/workspaces/<slug>``.

Legacy routing (``ws-<id>.<base>``) remains when tenant routing is off (see ``tenant_workspace_urls_enabled``).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from sqlmodel import Session, select

if TYPE_CHECKING:
    from app.libs.common.config import Settings
    from app.services.auth_service.models import UserAuth
    from app.services.workspace_service.models.workspace import Workspace

_logger = logging.getLogger(__name__)

_WORKSPACE_PATH_RE = re.compile(r"^/workspaces/([^/]+)")
_WS_LEGACY_HOST = re.compile(r"^ws-\d+", re.I)


def slugify_workspace_name(raw: str) -> str:
    """Lowercase URL slug: trim, spaces → hyphen, strip non [a-z0-9-], collapse hyphens."""
    s = (raw or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:128]


def effective_public_base_domain(settings: Settings) -> str:
    explicit = (settings.devnest_public_base_domain or "").strip().strip(".")
    if explicit:
        return explicit
    return (settings.devnest_base_domain or "").strip().strip(".")


def tenant_workspace_urls_enabled(settings: Settings) -> bool:
    """True when browser-facing URLs use per-user host + ``/workspaces/<slug>``."""
    raw = getattr(settings, "devnest_workspace_domain_mode", "") or ""
    mode = raw.strip().lower() if isinstance(raw, str) else ""
    if mode == "legacy":
        return False
    if mode == "tenant":
        return True
    return bool(settings.devnest_tenant_subdomain_routing_enabled)


def effective_public_scheme(settings: Settings) -> str:
    """Browser scheme for workspace IDE URLs.

    Explicit ``DEVNEST_PUBLIC_SCHEME`` always wins. When unset:
    - **Tenant** routing defaults to ``https`` (real-domain / TLS rollout).
    - **Legacy** routing follows ``DEVNEST_GATEWAY_PUBLIC_SCHEME`` (sslip + Traefik on :9081 stays HTTP).
    """
    raw = getattr(settings, "devnest_public_scheme", "") or ""
    pub = raw.strip().lower().rstrip(":") if isinstance(raw, str) else ""
    if pub:
        return pub
    if tenant_workspace_urls_enabled(settings):
        return "https"
    return (settings.devnest_gateway_public_scheme or "http").strip().lower().rstrip(":")


def effective_browser_port(settings: Settings) -> int:
    """Port segment for user-facing workspace URLs (not route-admin / Traefik debug URLs)."""
    raw = getattr(settings, "devnest_public_port", 0)
    try:
        explicit = int(raw or 0)
    except (TypeError, ValueError):
        explicit = 0
    if explicit > 0:
        return explicit
    if tenant_workspace_urls_enabled(settings):
        return 0
    try:
        return int(settings.devnest_gateway_public_port or 0)
    except (TypeError, ValueError):
        return 0


def gateway_public_host_with_port(host: str, scheme: str, port: int) -> str:
    if port <= 0:
        return host
    sch = (scheme or "http").strip().lower().rstrip(":")
    if sch == "http" and port == 80:
        return host
    if sch == "https" and port == 443:
        return host
    return f"{host}:{port}"


def build_workspace_url(*, user: UserAuth, workspace: Workspace, settings: Settings | None = None) -> str:
    """Absolute workspace IDE URL for the current routing mode."""
    from app.libs.common.config import get_settings

    s = settings or get_settings()
    scheme = effective_public_scheme(s)
    port = effective_browser_port(s)
    base_dom = effective_public_base_domain(s)

    tenant_on = tenant_workspace_urls_enabled(s)
    sub = (user.route_subdomain_slug or "").strip().lower()
    slug = (workspace.url_slug or "").strip().lower()

    if tenant_on and sub and slug:
        host = gateway_public_host_with_port(f"{sub}.{base_dom}", scheme, port)
        url = f"{scheme}://{host}/workspaces/{slug}"
        _logger.info(
            "routing.workspace_url_generated",
            extra={
                "workspace_id": workspace.workspace_id,
                "owner_user_id": workspace.owner_user_id,
                "scheme": scheme,
                "host": host,
                "path": f"/workspaces/{slug}",
                "mode": "tenant_subdomain",
            },
        )
        return url

    if tenant_on and not (sub and slug):
        _logger.warning(
            "routing.tenant_url_incomplete_fallback_legacy",
            extra={
                "workspace_id": workspace.workspace_id,
                "owner_user_id": workspace.owner_user_id,
                "has_route_subdomain": bool(sub),
                "has_url_slug": bool(slug),
            },
        )

    # Legacy: single-host per workspace (ws-<id>....)
    explicit = (workspace.public_host or "").strip()
    if explicit:
        host = gateway_public_host_with_port(explicit.split(":")[0], scheme, port)
    else:
        wid = workspace.workspace_id
        dom = (s.devnest_base_domain or "app.devnest.local").strip().strip(".")
        host = gateway_public_host_with_port(f"ws-{wid}.{dom}", scheme, port)
    url = f"{scheme}://{host}/"
    _logger.info(
        "routing.legacy_url_generated",
        extra={
            "workspace_id": workspace.workspace_id,
            "owner_user_id": workspace.owner_user_id,
            "scheme": scheme,
            "host": host,
            "mode": "legacy_ws_host",
        },
    )
    return url


def parse_workspace_host(host: str, base_domain: str) -> str | None:
    """Return the first DNS label (tenant route subdomain slug) before ``base_domain``, if any."""
    h = (host or "").strip().split(":")[0].lower()
    dom = (base_domain or "").strip().lower().strip(".")
    if not h or not dom:
        return None
    suffix = f".{dom}"
    if not h.endswith(suffix):
        return None
    label = h[: -len(suffix)].strip().strip(".")
    if not label or "." in label:
        return None
    return label


def extract_workspace_slug_from_path(path_or_uri: str) -> str | None:
    """Parse ``/workspaces/<slug>`` from a path or X-Forwarded-Uri value."""
    raw = (path_or_uri or "").strip()
    if not raw:
        return None
    path = raw.split("?", 1)[0]
    if path and path[0] != "/":
        try:
            from urllib.parse import urlparse

            path = urlparse(raw).path or "/"
        except Exception:
            path = "/" + raw.split("/", 1)[-1]
    m = _WORKSPACE_PATH_RE.match(path)
    if not m:
        return None
    return (m.group(1) or "").strip().lower() or None


def is_legacy_workspace_hostname(host_label_prefix: str) -> bool:
    return bool(_WS_LEGACY_HOST.match((host_label_prefix or "").strip()))


def allocate_unique_workspace_url_slug(session: Session, *, owner_user_id: int, display_name: str) -> str:
    """Reserve a unique ``url_slug`` per owner (among non-deleted workspaces)."""
    from app.services.workspace_service.models.enums import WorkspaceStatus
    from app.services.workspace_service.models.workspace import Workspace

    base = slugify_workspace_name(display_name) or "workspace"
    candidate = base
    n = 2
    while True:
        exists = session.exec(
            select(Workspace.workspace_id).where(
                Workspace.owner_user_id == owner_user_id,
                Workspace.url_slug == candidate,
                Workspace.status != WorkspaceStatus.DELETED.value,
            )
        ).first()
        if exists is None:
            return candidate
        candidate = f"{base}-{n}"
        n += 1


def allocate_unique_route_subdomain_slug(session: Session, *, preferred_username: str) -> str:
    """Reserve a unique ``UserAuth.route_subdomain_slug`` derived from username."""
    from app.services.auth_service.models import UserAuth

    base = slugify_workspace_name(preferred_username) or "user"
    candidate = base
    n = 2
    while True:
        exists = session.exec(
            select(UserAuth.user_auth_id).where(UserAuth.route_subdomain_slug == candidate),
        ).first()
        if exists is None:
            return candidate
        candidate = f"{base}-{n}"
        n += 1


def log_subdomain_parsed(*, forwarded_host: str, base_domain: str, subdomain: str | None) -> None:
    _logger.info(
        "routing.subdomain_parsed",
        extra={"forwarded_host": forwarded_host, "base_domain": base_domain, "subdomain": subdomain},
    )


def log_workspace_access_validated(
    *,
    workspace_id: int,
    owner_user_id: int,
    route_mode: str,
    subdomain: str | None = None,
    url_slug: str | None = None,
) -> None:
    _logger.info(
        "routing.workspace_access_validated",
        extra={
            "workspace_id": workspace_id,
            "owner_user_id": owner_user_id,
            "route_mode": route_mode,
            "subdomain": subdomain,
            "url_slug": url_slug,
        },
    )


def log_workspace_route_failed(*, reason: str, forwarded_host: str | None = None, detail: str | None = None) -> None:
    _logger.warning(
        "routing.workspace_route_failed",
        extra={"reason": reason, "forwarded_host": forwarded_host, "detail": detail},
    )
