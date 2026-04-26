"""Dedicated HTTP heartbeat loop for the default execution node (workspace-worker, Phase 3a).

POSTs to ``POST {INTERNAL_API_BASE_URL}/internal/execution-nodes/heartbeat`` on a fixed interval so
``execution_node.last_heartbeat_at`` is updated even when the job queue is idle. When
``DEVNEST_NODE_HEARTBEAT_ENABLED`` is false, this module is not used (see ``workspace_job_poll_loop``).
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.libs.common.config import get_settings
from app.libs.security.internal_auth import InternalApiScope, internal_api_expected_secrets
from app.services.placement_service.bootstrap import default_local_node_key
from app.services.placement_service.capacity import count_active_workloads_on_node_key
from app.services.placement_service.node_heartbeat import (
    _post_internal_execution_node_heartbeat_http,
    collect_local_execution_node_heartbeat_metrics,
    internal_api_execution_node_heartbeat_post_url,
)

logger = logging.getLogger(__name__)


def _infrastructure_internal_api_key() -> str:
    settings = get_settings()
    secrets = internal_api_expected_secrets(settings, InternalApiScope.INFRASTRUCTURE)
    if secrets and str(secrets[0] or "").strip():
        return str(secrets[0]).strip()
    return str(settings.internal_api_key or "").strip()


def _emit_one_heartbeat_http(engine: Engine, *, base_url: str, node_key: str) -> tuple[bool, str]:
    """Collect host metrics + slot count, then POST heartbeat. Returns (ok, error_detail)."""
    try:
        with Session(engine) as session:
            docker_ok, disk_free_mb, _slots_from_collect, version, _ = collect_local_execution_node_heartbeat_metrics(
                session
            )
            slots_in_use = int(count_active_workloads_on_node_key(session, node_key))
        ver = (version or "").strip()[:128] or (
            get_settings().devnest_execution_node_heartbeat_emitter_version or "worker-heartbeat-loop"
        ).strip()[:128]
        ok = _post_internal_execution_node_heartbeat_http(
            base_url=base_url,
            node_key=node_key,
            docker_ok=docker_ok,
            disk_free_mb=disk_free_mb,
            slots_in_use=slots_in_use,
            version=ver,
        )
        if ok:
            return True, ""
        return False, "non-2xx response (see prior execution_node_heartbeat_http_non_success log)"
    except Exception as e:  # noqa: BLE001 — caller logs execution_node_heartbeat_failure
        return False, str(e)[:500]


def _heartbeat_internal_api_base_url(settings: object) -> str:
    """Prefer ``INTERNAL_API_BASE_URL``; fall back to ``DEVNEST_WORKER_HEARTBEAT_INTERNAL_API_BASE_URL``."""
    s = settings
    primary = (getattr(s, "internal_api_base_url", "") or "").strip().rstrip("/")
    if primary:
        return primary
    return (getattr(s, "devnest_worker_heartbeat_internal_api_base_url", "") or "").strip().rstrip("/")


def run_execution_node_heartbeat_emitter_loop(engine: Engine, stop_event: threading.Event) -> None:
    """Blocking loop: emit immediately, then every ``DEVNEST_NODE_HEARTBEAT_INTERVAL_SECONDS`` until stopped."""
    settings = get_settings()
    base = _heartbeat_internal_api_base_url(settings)
    interval = max(5, min(3600, int(getattr(settings, "devnest_node_heartbeat_interval_seconds", 30) or 30)))
    node_key = (getattr(settings, "devnest_node_key", "") or "").strip() or default_local_node_key()
    api_key = _infrastructure_internal_api_key()

    # Human-facing strings for operator dashboards; ``extra`` retains structured fields.
    logger.info(
        "heartbeat emitter started",
        extra={
            "node_key": node_key,
            "interval_seconds": interval,
            "internal_api_base_url": base or None,
            "has_internal_api_key": bool(api_key),
            "event": "execution_node_heartbeat_emitter_started",
        },
    )

    if not base or not api_key:
        logger.warning(
            "heartbeat emitter misconfigured",
            extra={
                "node_key": node_key,
                "detail": "Set INTERNAL_API_BASE_URL and INTERNAL_API_KEY (or INTERNAL_API_KEY_INFRASTRUCTURE)",
                "has_internal_api_base_url": bool(base),
                "has_internal_api_key": bool(api_key),
                "event": "execution_node_heartbeat_emitter_misconfigured",
            },
        )
        return

    while not stop_event.is_set():
        ok, detail = _emit_one_heartbeat_http(engine, base_url=base, node_key=node_key)
        if ok:
            logger.info(
                "heartbeat success",
                extra={
                    "node_key": node_key,
                    "internal_api_base_url": base,
                    "heartbeat_post_url": internal_api_execution_node_heartbeat_post_url(base),
                    "event": "execution_node_heartbeat_success",
                },
            )
        else:
            logger.warning(
                "heartbeat failure",
                extra={
                    "node_key": node_key,
                    "detail": detail or "heartbeat post failed",
                    "event": "execution_node_heartbeat_failure",
                },
            )
        if stop_event.wait(timeout=float(interval)):
            break
