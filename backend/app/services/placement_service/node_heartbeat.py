"""Execution node heartbeat: control-plane liveness + capacity snapshot (Phase 3a)."""

from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.services.placement_service.bootstrap import default_local_node_key, ensure_default_local_execution_node
from app.services.placement_service.capacity import count_active_workloads_on_node_key
from app.services.placement_service.errors import ExecutionNodeNotFoundError
from app.services.placement_service.models import ExecutionNode
from app.services.placement_service.models.enums import ExecutionNodeStatus

_logger = logging.getLogger(__name__)

# POST path relative to FastAPI app root (``internal_execution_nodes_router`` prefix + route).
_INTERNAL_EXECUTION_NODES_HEARTBEAT_SUFFIX = "/internal/execution-nodes/heartbeat"
_INTERNAL_EXECUTION_NODES_PREFIX = "/internal/execution-nodes"


def internal_api_execution_node_heartbeat_post_url(base_url: str) -> str:
    """Build full heartbeat URL from ``INTERNAL_API_BASE_URL`` (or worker heartbeat base).

    Accepts ``http://backend:8000`` or a mistaken ``http://backend:8000/internal/execution-nodes``
    so callers do not double up path segments.
    """
    raw = (base_url or "").strip().rstrip("/")
    if not raw:
        return _INTERNAL_EXECUTION_NODES_HEARTBEAT_SUFFIX
    if raw.endswith(_INTERNAL_EXECUTION_NODES_PREFIX):
        return f"{raw}/heartbeat"
    return f"{raw}{_INTERNAL_EXECUTION_NODES_HEARTBEAT_SUFFIX}"


def _merge_heartbeat_metadata(existing: dict[str, Any] | None, payload: dict[str, Any]) -> dict[str, Any]:
    base = dict(existing or {})
    hb = dict(base.get("heartbeat") or {})
    hb.update(payload)
    base["heartbeat"] = hb
    return base


def record_execution_node_heartbeat(
    session: Session,
    *,
    node_key: str | None = None,
    execution_node_id: int | None = None,
    docker_ok: bool,
    disk_free_mb: int,
    slots_in_use: int,
    version: str,
) -> ExecutionNode:
    """Persist heartbeat fields on ``execution_node`` (idempotent row update)."""
    if execution_node_id is not None:
        row = session.get(ExecutionNode, execution_node_id)
        if row is None:
            raise ExecutionNodeNotFoundError(f"execution node id={execution_node_id} not found")
    elif node_key is not None and str(node_key).strip():
        key = str(node_key).strip()
        row = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == key)).first()
        if row is None:
            raise ExecutionNodeNotFoundError(f"execution node key={key!r} not found")
    else:
        raise ExecutionNodeNotFoundError("node_key or execution_node_id is required")

    now = datetime.now(timezone.utc)
    row.last_heartbeat_at = now
    row.updated_at = now

    hb_payload = {
        "received_at": now.isoformat(),
        "docker_ok": bool(docker_ok),
        "disk_free_mb": int(max(0, disk_free_mb)),
        "slots_in_use": int(max(0, slots_in_use)),
        "version": (version or "")[:128],
    }
    row.metadata_json = _merge_heartbeat_metadata(row.metadata_json, hb_payload)

    if docker_ok:
        row.last_error_code = None
        row.last_error_message = None
        st = str(row.status or "").strip().upper()
        if row.schedulable and st not in (
            ExecutionNodeStatus.DRAINING.value,
            ExecutionNodeStatus.TERMINATING.value,
            ExecutionNodeStatus.TERMINATED.value,
            ExecutionNodeStatus.PROVISIONING.value,
        ):
            # Healthy heartbeat on a schedulable node that is not draining: clear NOT_READY from liveness.
            if st == ExecutionNodeStatus.NOT_READY.value:
                row.status = ExecutionNodeStatus.READY.value
    else:
        row.last_error_code = "DOCKER_UNREACHABLE"
        row.last_error_message = "Heartbeat reported docker_ok=false"

    session.add(row)
    session.flush()
    return row


def collect_local_execution_node_heartbeat_metrics(session: Session) -> tuple[bool, int, int, str, str]:
    """Return (docker_ok, disk_free_mb, slots_in_use, version, node_key) for the default local node."""
    settings = get_settings()
    node = ensure_default_local_execution_node(session)
    key = (node.node_key or default_local_node_key()).strip() or "node-1"

    docker_ok = False
    try:
        import docker  # noqa: PLC0415

        docker.from_env().ping()
        docker_ok = True
    except Exception:
        docker_ok = False

    base = (settings.workspace_projects_base or "").strip()
    path = Path(base) if base else Path(tempfile.gettempdir())
    try:
        target = path if path.is_dir() else path.parent
        if not target.is_dir():
            target = Path(tempfile.gettempdir())
        du = shutil.disk_usage(str(target.resolve()))
        disk_free_mb = int(du.free // (1024 * 1024))
    except Exception:
        disk_free_mb = 0

    slots = int(count_active_workloads_on_node_key(session, key))
    ver = (settings.devnest_execution_node_heartbeat_emitter_version or "worker-embedded").strip()
    return docker_ok, disk_free_mb, slots, ver[:128], key


def _post_internal_execution_node_heartbeat_http(
    *,
    base_url: str,
    node_key: str,
    docker_ok: bool,
    disk_free_mb: int,
    slots_in_use: int,
    version: str,
) -> bool:
    """POST ``/internal/execution-nodes/heartbeat`` (infrastructure-scoped key). Returns True on 2xx."""
    from app.libs.security.internal_auth import InternalApiScope, internal_api_expected_secrets

    settings = get_settings()
    secrets = internal_api_expected_secrets(settings, InternalApiScope.INFRASTRUCTURE)
    if not secrets or not str(secrets[0] or "").strip():
        _logger.warning(
            "execution_node_heartbeat_http_skipped_no_internal_key",
            extra={"hint": "Set INTERNAL_API_KEY or INTERNAL_API_KEY_INFRASTRUCTURE for the worker"},
        )
        return False
    url = internal_api_execution_node_heartbeat_post_url(base_url)
    payload = {
        "node_key": node_key,
        "docker_ok": bool(docker_ok),
        "disk_free_mb": int(max(0, disk_free_mb)),
        "slots_in_use": int(max(0, slots_in_use)),
        "version": (version or "worker-embedded")[:128],
    }
    api_key = str(secrets[0]).strip()
    try:
        import httpx  # noqa: PLC0415

        resp = httpx.post(
            url,
            json=payload,
            headers={"X-Internal-API-Key": api_key},
            timeout=15.0,
        )
    except Exception:
        _logger.warning(
            "execution_node_heartbeat_http_request_error",
            extra={"url": url, "node_key": node_key},
            exc_info=True,
        )
        return False
    if 200 <= resp.status_code < 300:
        return True
    _logger.warning(
        "execution_node_heartbeat_http_non_success",
        extra={
            "url": url,
            "node_key": node_key,
            "status_code": resp.status_code,
            "body_preview": (resp.text or "")[:500],
        },
    )
    return False


def emit_default_local_execution_node_heartbeat(session: Session) -> None:
    """Worker hook: record heartbeat for the configured default local execution node."""
    settings = get_settings()
    if bool(getattr(settings, "devnest_node_heartbeat_enabled", False)):
        # Dedicated ``execution_node_heartbeat_emitter`` thread owns HTTP heartbeats.
        return
    if not bool(getattr(settings, "devnest_worker_emit_execution_node_heartbeat", True)):
        _logger.debug("execution_node_heartbeat_emit_disabled_by_settings")
        return
    docker_ok, disk_free_mb, slots, version, node_key = collect_local_execution_node_heartbeat_metrics(session)
    base = (getattr(settings, "devnest_worker_heartbeat_internal_api_base_url", "") or "").strip()
    if base:
        # Publish bootstrap row before the API handles a separate DB connection.
        session.commit()
        if _post_internal_execution_node_heartbeat_http(
            base_url=base,
            node_key=node_key,
            docker_ok=docker_ok,
            disk_free_mb=disk_free_mb,
            slots_in_use=slots,
            version=version,
        ):
            _logger.info(
                "execution_node_heartbeat_emitted_via_http",
                extra={
                    "node_key": node_key,
                    "docker_ok": docker_ok,
                    "disk_free_mb": disk_free_mb,
                    "slots_in_use": slots,
                    "version": version,
                    "transport": "http",
                },
            )
            return
        _logger.warning(
            "execution_node_heartbeat_http_fallback_db",
            extra={"node_key": node_key},
        )
    record_execution_node_heartbeat(
        session,
        node_key=node_key,
        docker_ok=docker_ok,
        disk_free_mb=disk_free_mb,
        slots_in_use=slots,
        version=version,
    )
    _logger.info(
        "execution_node_heartbeat_emitted",
        extra={
            "node_key": node_key,
            "docker_ok": docker_ok,
            "disk_free_mb": disk_free_mb,
            "slots_in_use": slots,
            "version": version,
            "transport": "db",
        },
    )


def try_emit_default_local_execution_node_heartbeat(engine: Engine) -> None:
    """Standalone worker / poll loop: one short DB session after job work; failures are logged only."""
    if bool(get_settings().devnest_node_heartbeat_enabled):
        return
    try:
        with Session(engine) as session:
            emit_default_local_execution_node_heartbeat(session)
            session.commit()
    except Exception:
        _logger.warning("execution_node_heartbeat_emit_failed", exc_info=True)


def heartbeat_freshness_seconds(session: Session, *, node_key: str | None = None) -> float | None:
    """Return age in seconds of ``last_heartbeat_at`` for the node, or None if missing."""
    key = (node_key or default_local_node_key()).strip() or "node-1"
    row = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == key)).first()
    if row is None or row.last_heartbeat_at is None:
        return None
    delta = datetime.now(timezone.utc) - row.last_heartbeat_at
    return max(0.0, delta.total_seconds())


def log_default_execution_node_heartbeat_diagnostics(session: Session) -> None:
    """Log whether the default local node's heartbeat looks fresh (non-fatal)."""
    settings = get_settings()
    max_age = int(getattr(settings, "devnest_node_heartbeat_max_age_seconds", 300) or 300)
    age = heartbeat_freshness_seconds(session)
    node = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == default_local_node_key())).first()
    nk = default_local_node_key()
    if age is None:
        _logger.warning(
            "execution_node_heartbeat_stale_or_missing",
            extra={
                "node_key": nk,
                "reason": "last_heartbeat_at is null",
                "max_age_seconds": max_age,
                "hint": "Ensure workspace-worker is running or POST /internal/execution-nodes/heartbeat",
            },
        )
        return
    if age > max_age:
        _logger.warning(
            "execution_node_heartbeat_stale_or_missing",
            extra={
                "node_key": nk,
                "age_seconds": round(age, 1),
                "max_age_seconds": max_age,
                "execution_node_id": getattr(node, "id", None),
            },
        )
    else:
        _logger.info(
            "execution_node_heartbeat_fresh",
            extra={"node_key": nk, "age_seconds": round(age, 1), "max_age_seconds": max_age},
        )


def execution_node_heartbeat_within_max_age(
    node: ExecutionNode,
    *,
    settings: Any | None = None,
) -> tuple[bool, str]:
    """Return ``(True, \"\")`` if ``last_heartbeat_at`` is within ``devnest_node_heartbeat_max_age_seconds``.

    Used for operator pinned CREATE (Phase 3b Step 8) regardless of
    ``DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT`` (that flag only affects normal scheduler placement).
    """
    s = settings or get_settings()
    max_age = int(getattr(s, "devnest_node_heartbeat_max_age_seconds", 300) or 300)
    ts = node.last_heartbeat_at
    if ts is None:
        return False, "last_heartbeat_at is null"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age)
    if ts < cutoff:
        age_s = (datetime.now(timezone.utc) - ts).total_seconds()
        return False, f"heartbeat age ≈ {age_s:.0f}s exceeds max_age_seconds={max_age}"
    return True, ""


def heartbeat_fresh_sql_predicates() -> list:
    """SQL predicates: node heartbeat is recent enough for placement (when gating is enabled)."""
    settings = get_settings()
    if not bool(getattr(settings, "devnest_require_fresh_node_heartbeat", False)):
        return []
    max_age = int(getattr(settings, "devnest_node_heartbeat_max_age_seconds", 300) or 300)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age)
    return [
        ExecutionNode.last_heartbeat_at.isnot(None),
        ExecutionNode.last_heartbeat_at >= cutoff,
    ]


def execution_node_heartbeat_age_seconds(node: ExecutionNode | None) -> int | None:
    """Seconds since ``last_heartbeat_at`` (UTC); ``None`` if never heartbeated (Phase 3b Step 12 ops logs)."""
    if node is None:
        return None
    ts = node.last_heartbeat_at
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return max(0, int((now - ts).total_seconds()))
