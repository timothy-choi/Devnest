"""Authenticated system status for operators and the UI (no secrets)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy import text
from sqlmodel import Session, select

from app.libs.common.config import database_host_and_name_for_log, get_settings
from app.libs.db.database import get_db, get_engine
from app.services.auth_service.api.dependencies import get_current_user
from app.services.auth_service.models import UserAuth
from app.services.storage.factory import snapshot_storage_log_fields
from app.services.workspace_service.models import WorkspaceJob
from app.services.workspace_service.models.enums import WorkspaceJobStatus
from app.workers.lifespan_worker import in_process_workspace_worker_running

router = APIRouter(prefix="/system", tags=["system"])


class SnapshotStorageStatusOut(BaseModel):
    provider: str
    bucket: str = ""
    prefix: str = ""
    region: str = ""
    root: str = ""


class GatewayStatusOut(BaseModel):
    enabled: bool
    base_domain: str
    public_scheme: str
    public_port: int = 0
    auth_enabled: bool
    route_admin_host: str = ""


class WorkerStatusOut(BaseModel):
    deployment_model: str = Field(
        description="in_process when API runs the poll loop; standalone when a separate worker is expected.",
    )
    in_process_enabled: bool
    in_process_task_running: bool | None = Field(
        default=None,
        description="True/False when deployment_model is in_process; null for standalone.",
    )
    jobs_queued: int = 0
    jobs_running: int = 0


class ApplicationStatusOut(BaseModel):
    devnest_env: str
    version: str | None = None
    git_commit: str | None = None


class SystemStatusResponse(BaseModel):
    backend_ok: bool = True
    database_connected: bool
    database_host: str
    database_name: str
    snapshot_storage: SnapshotStorageStatusOut
    gateway: GatewayStatusOut
    worker: WorkerStatusOut
    application: ApplicationStatusOut
    generated_at: str


def _safe_route_admin_host(gateway_url: str) -> str:
    raw = (gateway_url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    try:
        p = urlparse(raw)
        if p.hostname:
            if p.port and p.port not in (80, 443):
                return f"{p.hostname}:{p.port}"
            return p.hostname or ""
    except ValueError:
        return ""
    return ""


def _git_commit() -> str | None:
    for key in ("DEVNEST_GIT_COMMIT", "GITHUB_SHA", "SOURCE_COMMIT", "COMMIT_SHA"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v[:40]
    return None


def _app_version() -> str | None:
    for key in ("DEVNEST_APP_VERSION", "DEVNEST_VERSION"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    return None


def _job_counts(session: Session) -> tuple[int, int]:
    q_stmt = (
        select(func.count())
        .select_from(WorkspaceJob)
        .where(WorkspaceJob.status == WorkspaceJobStatus.QUEUED.value)
    )
    r_stmt = (
        select(func.count())
        .select_from(WorkspaceJob)
        .where(WorkspaceJob.status == WorkspaceJobStatus.RUNNING.value)
    )
    queued = int(session.exec(q_stmt).one())
    running = int(session.exec(r_stmt).one())
    return queued, running


@router.get(
    "/status",
    response_model=SystemStatusResponse,
    summary="System status (authenticated)",
    description="Deployment posture and dependency checks without secrets. Requires a valid user JWT.",
)
def get_system_status(
    session: Session = Depends(get_db),
    _current: UserAuth = Depends(get_current_user),
) -> SystemStatusResponse:
    settings = get_settings()
    db_host, db_name = database_host_and_name_for_log(settings.database_url)
    db_ok = False
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    snap_fields = snapshot_storage_log_fields()
    in_proc_enabled = bool(getattr(settings, "devnest_worker_enabled", False))
    in_proc_running = in_process_workspace_worker_running() if in_proc_enabled else None
    deployment_model = "in_process" if in_proc_enabled else "standalone"
    queued, running = _job_counts(session)

    return SystemStatusResponse(
        backend_ok=True,
        database_connected=db_ok,
        database_host=db_host,
        database_name=db_name,
        snapshot_storage=SnapshotStorageStatusOut(
            provider=str(snap_fields.get("provider") or "local"),
            bucket=str(snap_fields.get("bucket") or ""),
            prefix=str(snap_fields.get("prefix") or ""),
            region=str(snap_fields.get("region") or ""),
            root=str(snap_fields.get("root") or ""),
        ),
        gateway=GatewayStatusOut(
            enabled=bool(settings.devnest_gateway_enabled),
            base_domain=(settings.devnest_base_domain or "").strip(),
            public_scheme=(settings.devnest_gateway_public_scheme or "http").strip(),
            public_port=int(settings.devnest_gateway_public_port or 0),
            auth_enabled=bool(settings.devnest_gateway_auth_enabled),
            route_admin_host=_safe_route_admin_host(settings.devnest_gateway_url),
        ),
        worker=WorkerStatusOut(
            deployment_model=deployment_model,
            in_process_enabled=in_proc_enabled,
            in_process_task_running=in_proc_running,
            jobs_queued=queued,
            jobs_running=running,
        ),
        application=ApplicationStatusOut(
            devnest_env=(settings.devnest_env or "development").strip(),
            version=_app_version(),
            git_commit=_git_commit(),
        ),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
