"""Snapshot metadata, enqueue snapshot jobs, and storage coordination (control plane)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func
from sqlmodel import Session, select

from app.libs.observability.correlation import generate_correlation_id
from app.libs.observability.log_events import LogEvent, log_event
from app.libs.observability.metrics import record_job_queued
from app.services.storage.factory import get_snapshot_storage_provider
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
from app.services.workspace_service.services.workspace_event_service import (
    WorkspaceStreamEventType,
    assert_workspace_owner,
    record_workspace_event,
)

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
    assert_workspace_owner(session, workspace_id, owner_user_id)
    ws = session.get(Workspace, workspace_id)
    assert ws is not None
    if ws.status not in _SNAPSHOT_CREATE_ALLOWED:
        raise WorkspaceInvalidStateError(
            f"Snapshots can only be created for RUNNING or STOPPED workspaces (current={ws.status})",
        )
    if _pending_snapshot_jobs(session, workspace_id):
        raise SnapshotConflictError("A snapshot or restore job is already in progress for this workspace")

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
        LogEvent.WORKSPACE_SNAPSHOT_CREATED,
        correlation_id=cid,
        workspace_id=workspace_id,
        workspace_job_id=jid,
        workspace_snapshot_id=sid,
        phase="accepted",
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
    session.delete(snap)
    session.commit()


def restore_snapshot(
    session: Session,
    *,
    snapshot_id: int,
    owner_user_id: int,
    correlation_id: str | None = None,
) -> RestoreSnapshotResult:
    snap = _get_snapshot_for_owner(session, snapshot_id=snapshot_id, owner_user_id=owner_user_id)
    wid = snap.workspace_id
    assert wid is not None
    ws = session.get(Workspace, wid)
    assert ws is not None

    if snap.status != WorkspaceSnapshotStatus.AVAILABLE.value:
        raise WorkspaceInvalidStateError(f"Snapshot must be AVAILABLE (current={snap.status})")
    if ws.status != WorkspaceStatus.STOPPED.value:
        raise WorkspaceInvalidStateError("Workspace must be STOPPED before restore")
    if _pending_snapshot_jobs(session, wid):
        raise SnapshotConflictError("A snapshot or restore job is already in progress for this workspace")

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
    session.commit()
    session.refresh(snap)
    return RestoreSnapshotResult(
        workspace_id=wid,
        snapshot_id=int(snap.workspace_snapshot_id),
        job_id=jid,
        status=ws.status,
    )
