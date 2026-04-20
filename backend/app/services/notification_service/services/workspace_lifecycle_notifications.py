"""Workspace lifecycle notification helpers."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.services.notification_service.services import notification_service
from app.services.workspace_service.models import Workspace, WorkspaceJob, WorkspaceJobStatus, WorkspaceJobType

WORKSPACE_NOTIFICATION_TYPES: tuple[str, ...] = (
    "workspace.create.succeeded",
    "workspace.create.failed",
    "workspace.stop.succeeded",
    "workspace.stop.failed",
    "workspace.restart.succeeded",
    "workspace.restart.failed",
    "workspace.delete.succeeded",
    "workspace.delete.failed",
)

_WORKSPACE_JOB_NOTIFICATION_MAP: dict[tuple[str, str], tuple[str, str, str]] = {
    (WorkspaceJobType.CREATE.value, WorkspaceJobStatus.SUCCEEDED.value): (
        "workspace.create.succeeded",
        "Workspace created",
        "is ready to use.",
    ),
    (WorkspaceJobType.CREATE.value, WorkspaceJobStatus.FAILED.value): (
        "workspace.create.failed",
        "Workspace creation failed",
        "could not be created.",
    ),
    (WorkspaceJobType.STOP.value, WorkspaceJobStatus.SUCCEEDED.value): (
        "workspace.stop.succeeded",
        "Workspace stopped",
        "has been stopped.",
    ),
    (WorkspaceJobType.STOP.value, WorkspaceJobStatus.FAILED.value): (
        "workspace.stop.failed",
        "Workspace stop failed",
        "could not be stopped.",
    ),
    (WorkspaceJobType.RESTART.value, WorkspaceJobStatus.SUCCEEDED.value): (
        "workspace.restart.succeeded",
        "Workspace restarted",
        "is back online.",
    ),
    (WorkspaceJobType.RESTART.value, WorkspaceJobStatus.FAILED.value): (
        "workspace.restart.failed",
        "Workspace restart failed",
        "could not be restarted.",
    ),
    (WorkspaceJobType.DELETE.value, WorkspaceJobStatus.SUCCEEDED.value): (
        "workspace.delete.succeeded",
        "Workspace deleted",
        "has been deleted.",
    ),
    (WorkspaceJobType.DELETE.value, WorkspaceJobStatus.FAILED.value): (
        "workspace.delete.failed",
        "Workspace delete failed",
        "could not be deleted.",
    ),
}


def maybe_emit_workspace_lifecycle_notification(
    session: Session,
    *,
    workspace: Workspace,
    job: WorkspaceJob,
) -> None:
    if workspace.workspace_id is None:
        return
    mapping = _WORKSPACE_JOB_NOTIFICATION_MAP.get((job.job_type, job.status))
    if mapping is None:
        return

    notification_type, title, default_suffix = mapping
    body = f'"{workspace.name}" {default_suffix}'
    if job.status == WorkspaceJobStatus.FAILED.value and job.error_msg:
        body = f'{body} {job.error_msg}'

    payload: dict[str, Any] = {
        "workspace_id": workspace.workspace_id,
        "workspace_name": workspace.name,
        "workspace_status": workspace.status,
        "job_id": job.workspace_job_id,
        "job_type": job.job_type,
        "job_status": job.status,
    }
    if job.error_msg:
        payload["error_message"] = job.error_msg
    if workspace.last_error_code:
        payload["last_error_code"] = workspace.last_error_code

    notification_service.create_notification_event(
        session,
        type=notification_type,
        title=title,
        body=body,
        payload_json=payload,
        recipient_user_ids=[int(workspace.owner_user_id)],
        priority="HIGH" if job.status == WorkspaceJobStatus.FAILED.value else "NORMAL",
        source_service="workspace_job_worker",
        source_event_id=f"workspace_job:{job.workspace_job_id}:{notification_type}",
    )
