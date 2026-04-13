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

_lock = threading.Lock()
_routes: dict[str, dict[str, str]] = {}

_SAFE_WID = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")

app = FastAPI(title="DevNest route-admin", version="0.1.0")


class RouteRegisterBody(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=128)
    public_host: str = Field(min_length=1, max_length=512)
    target: str = Field(min_length=1, max_length=1024)


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
    entrypoint = "websecure" if _TLS_ENABLED else "web"
    for wid, row in sorted(_routes.items(), key=lambda kv: kv[0]):
        rname = _router_name(wid)
        router_def: dict[str, Any] = {
            "rule": f"Host(`{row['public_host']}`)",
            "entryPoints": [entrypoint],
            "service": f"{rname}-upstream",
        }
        if _GATEWAY_AUTH_ENABLED:
            router_def["middlewares"] = ["devnest-workspace-auth@file"]
        if _TLS_ENABLED:
            router_def["tls"] = {}
        routers[rname] = router_def
        services[f"{rname}-upstream"] = {
            "loadBalancer": {"servers": [{"url": row["target"]}]},
        }
    doc = {"http": {"routers": routers, "services": services}}
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
    row = {"workspace_id": wid, "public_host": public_host, "target": target}
    with _lock:
        _routes[wid] = row
        _persist_locked()
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
