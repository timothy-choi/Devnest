"""Snapshot metadata, enqueue snapshot jobs, and storage coordination (control plane)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import func
from sqlmodel import Session, select

from app.libs.observability.correlation import generate_correlation_id
from app.libs.observability.log_events import LogEvent, log_event
from app.libs.observability.metrics import record_job_queued
from app.services.storage.factory import get_snapshot_storage_provider
from app.services.storage.s3_storage import S3SnapshotStorageProvider
from app.services.workspace_service.errors import (
    SnapshotConflictError,
    SnapshotNotFoundError,
    WorkspaceInvalidStateError,
)
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceJob,
    WorkspaceSnapshot,
)
from app.services.workspace_service.models.enums import (
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceSnapshotStatus,
    WorkspaceStatus,
)
from app.services.workspace_service.services.snapshot_download_token import (
    create_snapshot_archive_download_token,
)
from app.services.workspace_service.services.workspace_event_service import (
    WorkspaceStreamEventType,
    assert_workspace_owner,
    record_workspace_event,
)
from app.services.audit_service.enums import AuditAction, AuditActorType, AuditOutcome
from app.services.audit_service.service import record_audit
from app.services.usage_service.enums import UsageEventType
from app.services.usage_service.service import record_usage
from app.services.policy_service.service import evaluate_snapshot_creation
from app.services.quota_service.service import check_snapshot_quota

logger = logging.getLogger(__name__)

_SNAPSHOT_CREATE_ALLOWED = frozenset(
    {
        WorkspaceStatus.RUNNING.value,
        WorkspaceStatus.STOPPED.value,
    },
)


def _latest_config_version(session: Session, workspace_id: int) -> int:
    v = session.exec(
        select(func.max(WorkspaceConfig.version)).where(WorkspaceConfig.workspace_id == workspace_id),
    ).one()
    return int(v or 1)


def _pending_snapshot_jobs(session: Session, workspace_id: int) -> bool:
    row = session.exec(
        select(WorkspaceJob.workspace_job_id).where(
            WorkspaceJob.workspace_id == workspace_id,
            WorkspaceJob.status.in_(
                (
                    WorkspaceJobStatus.QUEUED.value,
                    WorkspaceJobStatus.RUNNING.value,
                ),
            ),
            WorkspaceJob.job_type.in_(
                (
                    WorkspaceJobType.SNAPSHOT_CREATE.value,
                    WorkspaceJobType.SNAPSHOT_RESTORE.value,
                ),
            ),
        ),
    ).first()
    return row is not None


def _get_snapshot_for_owner(
    session: Session,
    *,
    snapshot_id: int,
    owner_user_id: int,
) -> WorkspaceSnapshot:
    snap = session.get(WorkspaceSnapshot, snapshot_id)
    if snap is None:
        raise SnapshotNotFoundError("Snapshot not found")
    ws = session.get(Workspace, snap.workspace_id)
    if ws is None or ws.owner_user_id != owner_user_id:
        raise SnapshotNotFoundError("Snapshot not found")
    return snap


@dataclass(frozen=True, slots=True)
class CreateSnapshotResult:
    workspace_id: int
    snapshot_id: int
    job_id: int
    status: str


@dataclass(frozen=True, slots=True)
class RestoreSnapshotResult:
    workspace_id: int
    snapshot_id: int
    job_id: int
    status: str


@dataclass(frozen=True, slots=True)
class SnapshotArchiveDownloadInfo:
    """Local path to a ``.tar.gz`` archive and how to serve it to the user."""

    local_path: str
    suggested_filename: str
    cleanup_after_send: bool


@dataclass(frozen=True, slots=True)
class SnapshotArchiveDownloadOffer:
    """How the browser should download bytes without proxying the archive through Next.js."""

    mode: Literal["presigned_s3", "backend_direct"]
    filename: str
    expires_in: int
    presigned_url: str | None = None
    relative_url: str | None = None


def _resolve_owned_snapshot_archive_for_download(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    snapshot_id: int | None,
) -> tuple[int, int]:
    """Return ``(workspace_id, snapshot_id)`` for an AVAILABLE snapshot with a non-empty archive."""
    assert_workspace_owner(session, workspace_id, owner_user_id)
    storage = get_snapshot_storage_provider()

    snap: WorkspaceSnapshot | None
    if snapshot_id is not None:
        snap = _get_snapshot_for_owner(session, snapshot_id=snapshot_id, owner_user_id=owner_user_id)
        if int(snap.workspace_id) != int(workspace_id):
            raise SnapshotNotFoundError("Snapshot not found")
    else:
        snap = None
        for row in list_snapshots(session, workspace_id=workspace_id, owner_user_id=owner_user_id):
            if row.status != WorkspaceSnapshotStatus.AVAILABLE.value:
                continue
            sid = row.workspace_snapshot_id
            assert sid is not None
            if storage.has_nonempty_archive(workspace_id=workspace_id, snapshot_id=sid):
                snap = row
                break
        if snap is None:
            raise SnapshotNotFoundError(
                "No snapshot with a completed archive is available yet. Save the workspace first, "
                "wait for the snapshot job to finish, then try again."
            )

    sid_final = snap.workspace_snapshot_id
    assert sid_final is not None
    wid = snap.workspace_id
    assert wid is not None

    if snap.status != WorkspaceSnapshotStatus.AVAILABLE.value:
        raise WorkspaceInvalidStateError(f"Snapshot must be AVAILABLE (current={snap.status})")

    if not storage.has_nonempty_archive(workspace_id=wid, snapshot_id=sid_final):
        raise WorkspaceInvalidStateError(
            "Snapshot archive is missing or empty on storage; try saving again once the workspace is RUNNING or STOPPED.",
        )

    return int(wid), int(sid_final)


def create_snapshot(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    name: str,
    description: str | None = None,
    metadata: dict | None = None,
    correlation_id: str | None = None,
) -> CreateSnapshotResult:
    """Enqueue ``SNAPSHOT_CREATE``; the worker resolves the execution host from runtime / placement (Phase 3b Step 10)."""
    assert_workspace_owner(session, workspace_id, owner_user_id)
    ws = session.get(Workspace, workspace_id)
    assert ws is not None
    if ws.status not in _SNAPSHOT_CREATE_ALLOWED:
        raise WorkspaceInvalidStateError(
            f"Snapshots can only be created for RUNNING or STOPPED workspaces (current={ws.status})",
        )
    if _pending_snapshot_jobs(session, workspace_id):
        raise SnapshotConflictError("A snapshot or restore job is already in progress for this workspace")

    # Quota + policy checks before creating the snapshot job
    check_snapshot_quota(
        session,
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        correlation_id=correlation_id,
    )
    evaluate_snapshot_creation(
        session,
        owner_user_id=owner_user_id,
        workspace_id=workspace_id,
        correlation_id=correlation_id,
    )

    cfg_v = _latest_config_version(session, workspace_id)
    cid = (correlation_id or generate_correlation_id()).strip() or generate_correlation_id()
    storage = get_snapshot_storage_provider()

    snap = WorkspaceSnapshot(
        workspace_id=workspace_id,
        name=name.strip()[:255],
        description=(description or "").strip()[:8192] or None,
        storage_uri="pending",
        status=WorkspaceSnapshotStatus.CREATING.value,
        created_by_user_id=owner_user_id,
        metadata_json=dict(metadata or {}),
    )
    session.add(snap)
    session.flush()
    sid = snap.workspace_snapshot_id
    assert sid is not None
    snap.storage_uri = storage.storage_uri(workspace_id=workspace_id, snapshot_id=sid)

    job = WorkspaceJob(
        workspace_id=workspace_id,
        job_type=WorkspaceJobType.SNAPSHOT_CREATE.value,
        status=WorkspaceJobStatus.QUEUED.value,
        requested_by_user_id=owner_user_id,
        requested_config_version=cfg_v,
        attempt=0,
        correlation_id=cid,
        workspace_snapshot_id=sid,
    )
    session.add(job)
    session.flush()
    jid = job.workspace_job_id
    assert jid is not None

    record_job_queued(job_type=WorkspaceJobType.SNAPSHOT_CREATE.value)
    log_event(
        logger,
        LogEvent.WORKSPACE_SNAPSHOT_REQUESTED,
        correlation_id=cid,
        workspace_id=workspace_id,
        workspace_job_id=jid,
        workspace_snapshot_id=sid,
    )
    record_workspace_event(
        session,
        workspace_id=workspace_id,
        event_type=WorkspaceStreamEventType.INTENT_QUEUED,
        status=ws.status,
        message="Snapshot create job queued",
        payload={
            "job_id": jid,
            "job_type": WorkspaceJobType.SNAPSHOT_CREATE.value,
            "workspace_snapshot_id": sid,
            "requested_config_version": cfg_v,
        },
    )
    record_audit(
        session,
        action=AuditAction.WORKSPACE_SNAPSHOT_CREATE_REQUESTED.value,
        resource_type="workspace_snapshot",
        resource_id=sid,
        actor_user_id=owner_user_id,
        actor_type=AuditActorType.USER.value,
        outcome=AuditOutcome.SUCCESS.value,
        workspace_id=workspace_id,
        job_id=jid,
        correlation_id=cid,
        metadata={"snapshot_name": name.strip()[:255]},
    )
    session.commit()
    session.refresh(snap)
    return CreateSnapshotResult(
        workspace_id=workspace_id,
        snapshot_id=sid,
        job_id=jid,
        status=snap.status,
    )


def list_snapshots(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
) -> list[WorkspaceSnapshot]:
    assert_workspace_owner(session, workspace_id, owner_user_id)
    rows = session.exec(
        select(WorkspaceSnapshot)
        .where(WorkspaceSnapshot.workspace_id == workspace_id)
        .order_by(WorkspaceSnapshot.created_at.desc()),
    ).all()
    return list(rows)


def get_snapshot(
    session: Session,
    *,
    snapshot_id: int,
    owner_user_id: int,
) -> WorkspaceSnapshot:
    return _get_snapshot_for_owner(session, snapshot_id=snapshot_id, owner_user_id=owner_user_id)


def delete_snapshot(
    session: Session,
    *,
    snapshot_id: int,
    owner_user_id: int,
) -> None:
    snap = _get_snapshot_for_owner(session, snapshot_id=snapshot_id, owner_user_id=owner_user_id)
    wid = snap.workspace_id
    sid = snap.workspace_snapshot_id
    assert sid is not None and wid is not None

    pending = session.exec(
        select(WorkspaceJob.workspace_job_id).where(
            WorkspaceJob.workspace_snapshot_id == sid,
            WorkspaceJob.status.in_(
                (
                    WorkspaceJobStatus.QUEUED.value,
                    WorkspaceJobStatus.RUNNING.value,
                ),
            ),
        ),
    ).first()
    if pending is not None:
        raise SnapshotConflictError("Cannot delete snapshot while a job references it")

    storage = get_snapshot_storage_provider()
    storage.delete_archive(workspace_id=wid, snapshot_id=sid)

    ws = session.get(Workspace, wid)
    assert ws is not None
    record_workspace_event(
        session,
        workspace_id=wid,
        event_type=WorkspaceStreamEventType.SNAPSHOT_DELETED,
        status=ws.status,
        message="Workspace snapshot deleted",
        payload={"workspace_snapshot_id": sid},
    )
    log_event(
        logger,
        LogEvent.WORKSPACE_SNAPSHOT_DELETED,
        workspace_id=wid,
        workspace_snapshot_id=sid,
    )
    record_audit(
        session,
        action=AuditAction.WORKSPACE_SNAPSHOT_DELETED.value,
        resource_type="workspace_snapshot",
        resource_id=sid,
        actor_user_id=owner_user_id,
        actor_type=AuditActorType.USER.value,
        outcome=AuditOutcome.SUCCESS.value,
        workspace_id=wid,
    )
    session.delete(snap)
    session.commit()


def prepare_snapshot_archive_download(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    snapshot_id: int | None = None,
    correlation_id: str | None = None,
) -> SnapshotArchiveDownloadInfo:
    """Resolve an AVAILABLE snapshot with a non-empty archive and materialize a local file for download.

    For S3-backed storage, downloads the object to the provider staging path (same as restore preflight).
    For local storage, returns the canonical archive path under the snapshot root.
    """
    wid, sid_final = _resolve_owned_snapshot_archive_for_download(
        session,
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        snapshot_id=snapshot_id,
    )
    storage = get_snapshot_storage_provider()

    cleanup = False
    if hasattr(storage, "download_archive"):
        storage.download_archive(workspace_id=wid, snapshot_id=sid_final)
        cleanup = True

    local_path = storage.archive_path(workspace_id=wid, snapshot_id=sid_final)
    if not Path(local_path).is_file() or Path(local_path).stat().st_size <= 0:
        raise WorkspaceInvalidStateError("Snapshot archive file is missing or empty after storage sync")

    name = f"workspace-{wid}-snapshot-{sid_final}.tar.gz"
    log_event(
        logger,
        LogEvent.WORKSPACE_SNAPSHOT_DOWNLOAD_REQUESTED,
        correlation_id=correlation_id,
        workspace_id=int(wid),
        workspace_snapshot_id=int(sid_final),
    )
    return SnapshotArchiveDownloadInfo(
        local_path=local_path,
        suggested_filename=name,
        cleanup_after_send=cleanup,
    )


def build_snapshot_archive_download_offer(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    snapshot_id: int | None = None,
    correlation_id: str | None = None,
) -> SnapshotArchiveDownloadOffer:
    """Return a presigned S3 URL or a backend-relative URL with a short-lived download token (local).

    Does not download S3 objects onto the API host — suitable for large archives.
    """
    wid, sid_final = _resolve_owned_snapshot_archive_for_download(
        session,
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        snapshot_id=snapshot_id,
    )
    storage = get_snapshot_storage_provider()
    filename = f"workspace-{wid}-snapshot-{sid_final}.tar.gz"
    log_event(
        logger,
        LogEvent.WORKSPACE_SNAPSHOT_DOWNLOAD_REQUESTED,
        correlation_id=correlation_id,
        workspace_id=int(wid),
        workspace_snapshot_id=int(sid_final),
    )

    if isinstance(storage, S3SnapshotStorageProvider):
        expires_in = 900
        url = storage.presign_archive_get_url(
            workspace_id=wid,
            snapshot_id=sid_final,
            filename=filename,
            expires_in=expires_in,
        )
        return SnapshotArchiveDownloadOffer(
            mode="presigned_s3",
            filename=filename,
            expires_in=expires_in,
            presigned_url=url,
            relative_url=None,
        )

    expires_in = 600
    token = create_snapshot_archive_download_token(
        workspace_id=wid,
        snapshot_id=sid_final,
        user_auth_id=owner_user_id,
        ttl_seconds=expires_in,
    )
    rel = f"/workspaces/{wid}/snapshots/archive?download_token={token}"
    return SnapshotArchiveDownloadOffer(
        mode="backend_direct",
        filename=filename,
        expires_in=expires_in,
        presigned_url=None,
        relative_url=rel,
    )


def restore_snapshot(
    session: Session,
    *,
    snapshot_id: int,
    owner_user_id: int,
    correlation_id: str | None = None,
) -> RestoreSnapshotResult:
    """Queue ``SNAPSHOT_RESTORE`` (workspace must be STOPPED).

    Restores **bind-mounted project files** via the orchestrator import path only. Control-plane
    config rows and runtime records are unchanged; a follow-up start/update/reconcile may be needed.

    Preflight: archive must exist on the configured storage provider with non-zero size so we do
    not enqueue a job that would always fail (multi-node: execution host must reach the same blob).
    """
    snap = _get_snapshot_for_owner(session, snapshot_id=snapshot_id, owner_user_id=owner_user_id)
    wid = snap.workspace_id
    assert wid is not None
    sid = snap.workspace_snapshot_id
    assert sid is not None
    ws = session.get(Workspace, wid)
    assert ws is not None

    if snap.status != WorkspaceSnapshotStatus.AVAILABLE.value:
        raise WorkspaceInvalidStateError(f"Snapshot must be AVAILABLE (current={snap.status})")
    if ws.status != WorkspaceStatus.STOPPED.value:
        raise WorkspaceInvalidStateError("Workspace must be STOPPED before restore")
    if _pending_snapshot_jobs(session, wid):
        raise SnapshotConflictError("A snapshot or restore job is already in progress for this workspace")

    storage = get_snapshot_storage_provider()
    if not storage.has_nonempty_archive(workspace_id=wid, snapshot_id=sid):
        log_event(
            logger,
            LogEvent.WORKSPACE_SNAPSHOT_FAILED,
            workspace_id=wid,
            workspace_snapshot_id=sid,
            phase="restore_preflight",
            detail="archive_missing_or_empty",
        )
        raise WorkspaceInvalidStateError(
            "Snapshot archive is missing or empty on storage; cannot restore (check storage root / drift)",
        )

    cfg_v = _latest_config_version(session, wid)
    cid = (correlation_id or generate_correlation_id()).strip() or generate_correlation_id()

    snap.status = WorkspaceSnapshotStatus.RESTORING.value
    session.add(snap)

    job = WorkspaceJob(
        workspace_id=wid,
        job_type=WorkspaceJobType.SNAPSHOT_RESTORE.value,
        status=WorkspaceJobStatus.QUEUED.value,
        requested_by_user_id=owner_user_id,
        requested_config_version=cfg_v,
        attempt=0,
        correlation_id=cid,
        workspace_snapshot_id=snap.workspace_snapshot_id,
    )
    session.add(job)
    session.flush()
    jid = job.workspace_job_id
    assert jid is not None

    record_job_queued(job_type=WorkspaceJobType.SNAPSHOT_RESTORE.value)
    log_event(
        logger,
        LogEvent.WORKSPACE_JOB_QUEUED,
        correlation_id=cid,
        workspace_id=wid,
        workspace_job_id=jid,
        job_type=WorkspaceJobType.SNAPSHOT_RESTORE.value,
    )
    record_workspace_event(
        session,
        workspace_id=wid,
        event_type=WorkspaceStreamEventType.INTENT_QUEUED,
        status=ws.status,
        message="Snapshot restore job queued",
        payload={
            "job_id": jid,
            "job_type": WorkspaceJobType.SNAPSHOT_RESTORE.value,
            "workspace_snapshot_id": snap.workspace_snapshot_id,
            "requested_config_version": cfg_v,
        },
    )
    record_audit(
        session,
        action=AuditAction.WORKSPACE_SNAPSHOT_RESTORE_REQUESTED.value,
        resource_type="workspace_snapshot",
        resource_id=sid,
        actor_user_id=owner_user_id,
        actor_type=AuditActorType.USER.value,
        outcome=AuditOutcome.SUCCESS.value,
        workspace_id=wid,
        job_id=jid,
        correlation_id=cid,
    )
    session.commit()
    session.refresh(snap)
    return RestoreSnapshotResult(
        workspace_id=wid,
        snapshot_id=int(snap.workspace_snapshot_id),
        job_id=jid,
        status=ws.status,
    )
