"""
Route registration API (control plane → data plane, V1).

Persists Traefik file-provider fragments to ROUTES_FILE (merged with static routes in the same
directory). TODO: HA / leader election, reconcile with backend truth.

ForwardAuth middleware:
  When DEVNEST_GATEWAY_AUTH_ENABLED=true (default: false), each workspace router gets
  the ``devnest-workspace-auth@file`` middleware attached so Traefik validates workspace
  session tokens via the backend's ``GET /internal/gateway/auth`` endpoint before proxying.
  The middleware is defined in traefik/dynamic/000-base.yml.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Container default: shared volume with Traefik. Override in tests via monkeypatch.
ROUTES_FILE = Path(os.environ.get("ROUTES_FILE", "/etc/traefik/dynamic/100-workspaces.yml"))

# When true, attach devnest-workspace-auth@file middleware to every workspace router.
# Must match DEVNEST_GATEWAY_AUTH_ENABLED in the backend config.
_GATEWAY_AUTH_ENABLED: bool = os.environ.get("DEVNEST_GATEWAY_AUTH_ENABLED", "").strip().lower() in (
    "1", "true", "yes", "on",
)

# When true, generated routers use the ``websecure`` entrypoint instead of ``web``.
_TLS_ENABLED: bool = os.environ.get("DEVNEST_TLS_ENABLED", "").strip().lower() in (
    "1", "true", "yes", "on",
)


def _env_scheme_https() -> bool:
    s = (os.environ.get("DEVNEST_GATEWAY_PUBLIC_SCHEME") or "").strip().lower()
    return s == "https"


def _workspace_https_enabled() -> bool:
    """HTTPS on workspace routers when legacy TLS flag is on or public scheme is https."""
    return _TLS_ENABLED or _env_scheme_https()


def _base_domain() -> str:
    return (os.environ.get("DEVNEST_BASE_DOMAIN") or "").strip().lower()


def _use_dns_wildcard_acme() -> bool:
    """Use Let's Encrypt DNS wildcard (Cloudflare) for workspace TLS when domain is not sslip-only."""
    if not _workspace_https_enabled():
        return False
    bd = _base_domain()
    if not bd or "sslip.io" in bd:
        return False
    return True


def _tls_router_options() -> dict[str, Any]:
    """TLS block for websecure workspace routers (self-signed vs ACME DNS wildcard)."""
    if not _workspace_https_enabled():
        return {}
    if _use_dns_wildcard_acme():
        bd = _base_domain()
        return {
            "certResolver": "letsencrypt-dns",
            "domains": [{"main": f"*.{bd}", "sans": [bd]}],
        }
    return {}


def _wid_from_router_name(rname: str) -> str | None:
    """Parse workspace id from ``devnest-reg-<id>`` or HTTP mirror ``devnest-reg-<id>-http``."""
    if not isinstance(rname, str) or not rname.startswith("devnest-reg-"):
        return None
    rest = rname[len("devnest-reg-") :]
    if rest.endswith("-http"):
        rest = rest[: -len("-http")]
    if not rest or not _SAFE_WID.match(rest):
        return None
    return rest


_lock = threading.Lock()
_routes: dict[str, dict[str, str]] = {}

_SAFE_WID = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")
_HOST_RULE = re.compile(r"^Host\(`([^`]+)`\)\s*$")
_HOST_RULE_PREFIX = re.compile(r"^Host\(`([^`]+)`\)\s*&&\s*PathPrefix\(`([^`]+)`\)\s*$")


def _hydrate_routes_from_disk_locked() -> None:
    """Load persisted workspace routes into memory so GET /routes and Traefik match after container restart."""
    path = ROUTES_FILE
    if not path.is_file():
        return
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(
            "route_admin_hydrate_parse_failed",
            extra={"path": str(path), "error": str(e)},
        )
        return
    if not isinstance(raw, dict):
        return
    http = raw.get("http")
    if not isinstance(http, dict):
        return
    routers = http.get("routers") or {}
    services = http.get("services") or {}
    if not isinstance(routers, dict) or not isinstance(services, dict):
        return
    prefix = "devnest-reg-"
    loaded = 0
    names_by_wid: dict[str, list[str]] = {}
    for rname in routers:
        wid = _wid_from_router_name(rname)
        if wid is None:
            continue
        names_by_wid.setdefault(wid, []).append(rname)

    for wid, names in names_by_wid.items():
        primary = f"{prefix}{wid}"
        if primary in routers:
            rname = primary
        else:
            http_mirror = f"{primary}-http"
            rname = http_mirror if http_mirror in routers else names[0]
        rdef = routers[rname]
        if not isinstance(rdef, dict):
            continue
        rule = str(rdef.get("rule") or "").strip()
        public_host = ""
        path_prefix = ""
        m2 = _HOST_RULE_PREFIX.match(rule)
        if m2:
            public_host = m2.group(1).strip()
            path_prefix = m2.group(2).strip()
        else:
            m = _HOST_RULE.match(rule)
            if not m:
                continue
            public_host = m.group(1).strip()
        svc_name = f"{prefix}{wid}-upstream"
        svc = services.get(svc_name)
        if not isinstance(svc, dict):
            continue
        lb = svc.get("loadBalancer") or {}
        if not isinstance(lb, dict):
            continue
        servers = lb.get("servers") or []
        if not servers or not isinstance(servers, list):
            continue
        first = servers[0]
        if not isinstance(first, dict):
            continue
        target = str(first.get("url") or "").strip()
        if not target:
            continue
        try:
            target = _normalize_target(target)
        except ValueError:
            continue
        _routes[wid] = {
            "workspace_id": wid,
            "public_host": public_host,
            "target": target,
            "path_prefix": path_prefix,
        }
        loaded += 1
    if loaded:
        logger.info("route_admin_hydrated", extra={"path": str(path), "count": loaded})


@asynccontextmanager
async def _lifespan(app: FastAPI):
    with _lock:
        _hydrate_routes_from_disk_locked()
    yield


app = FastAPI(title="DevNest route-admin", version="0.1.0", lifespan=_lifespan)


class RouteRegisterBody(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=128)
    public_host: str = Field(min_length=1, max_length=512)
    target: str = Field(min_length=1, max_length=1024)
    path_prefix: str | None = Field(default=None, max_length=512)
    # Optional metadata for ops logs (not written into Traefik YAML).
    node_key: str | None = Field(default=None, max_length=256)
    execution_node_id: int | None = Field(default=None, ge=1)


def _normalize_target(raw: str) -> str:
    t = (raw or "").strip()
    if not t:
        raise ValueError("target is empty")
    if "://" not in t:
        t = f"http://{t}"
    if not (t.startswith("http://") or t.startswith("https://")):
        raise ValueError("target must be http(s) URL or host:port")
    return t


def _router_name(workspace_id: str) -> str:
    return f"devnest-reg-{workspace_id}"


def _persist_locked() -> None:
    path = ROUTES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if not _routes:
        # Empty root document: must not set http.routers: {} or Traefik merge can drop sibling routes.
        fd, tmp = tempfile.mkstemp(
            prefix=".routes-",
            suffix=".yml",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("# Managed by route-admin - no workspace routes.\n{}\n")
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        logger.info("route_admin_persisted_empty", extra={"path": str(path)})
        return

    routers: dict[str, Any] = {}
    services: dict[str, Any] = {}
    middlewares: dict[str, Any] = {}
    https_on = _workspace_https_enabled()
    for wid, row in sorted(_routes.items(), key=lambda kv: kv[0]):
        rname = _router_name(wid)
        path_prefix = (row.get("path_prefix") or "").strip()
        rule = f"Host(`{row['public_host']}`)"
        mids: list[str] = []
        if path_prefix:
            rule += f" && PathPrefix(`{path_prefix}`)"
            strip_name = f"{rname}-stripprefix"
            middlewares[strip_name] = {"stripPrefix": {"prefixes": [path_prefix]}}
            mids.append(strip_name)
        if _GATEWAY_AUTH_ENABLED:
            mids.insert(0, "devnest-workspace-auth@file")

        def _router_def(entrypoint: str, *, with_tls: bool) -> dict[str, Any]:
            d: dict[str, Any] = {
                "rule": rule,
                "entryPoints": [entrypoint],
                "service": f"{rname}-upstream",
            }
            if mids:
                d["middlewares"] = mids
            if with_tls:
                tls_opts = _tls_router_options()
                if tls_opts:
                    d["tls"] = tls_opts
                else:
                    d["tls"] = {}
            return d

        if https_on:
            routers[rname] = _router_def("websecure", with_tls=True)
            routers[f"{rname}-http"] = _router_def("web", with_tls=False)
        else:
            routers[rname] = _router_def("web", with_tls=False)

        services[f"{rname}-upstream"] = {
            "loadBalancer": {"servers": [{"url": row["target"]}]},
        }
    doc: dict[str, Any] = {"http": {"routers": routers, "services": services}}
    if middlewares:
        doc["http"]["middlewares"] = middlewares
    fd, tmp = tempfile.mkstemp(
        prefix=".routes-",
        suffix=".yml",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("# Managed by route-admin (backend registration). Do not edit by hand.\n")
            yaml.safe_dump(doc, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    logger.info("route_admin_persisted", extra={"path": str(path), "count": len(_routes)})


@app.post("/routes")
def register_route(body: RouteRegisterBody) -> dict[str, str]:
    wid = body.workspace_id.strip()
    if not _SAFE_WID.match(wid):
        raise HTTPException(status_code=400, detail="invalid workspace_id")
    try:
        target = _normalize_target(body.target)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    public_host = body.public_host.strip()
    path_prefix = (body.path_prefix or "").strip()
    row = {
        "workspace_id": wid,
        "public_host": public_host,
        "target": target,
        "path_prefix": path_prefix,
    }
    with _lock:
        _routes[wid] = row
        _persist_locked()
    logger.info(
        "route_admin_route_upserted",
        extra={
            "workspace_id": wid,
            "public_host": public_host,
            "path_prefix": path_prefix or "",
            "target": target,
            "node_key": (body.node_key or "").strip() or None,
            "execution_node_id": body.execution_node_id,
        },
    )
    return row


@app.delete("/routes/{workspace_id}")
def deregister_route(workspace_id: str) -> Response:
    wid = workspace_id.strip()
    if not wid:
        raise HTTPException(status_code=400, detail="empty workspace_id")
    with _lock:
        _routes.pop(wid, None)
        _persist_locked()
    return Response(status_code=204)


@app.get("/routes")
def list_routes() -> list[dict[str, str]]:
    with _lock:
        return [dict(r) for r in sorted(_routes.values(), key=lambda x: x["workspace_id"])]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
