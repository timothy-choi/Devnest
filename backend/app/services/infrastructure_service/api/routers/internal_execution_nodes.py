"""
Internal admin routes: EC2 provisioning and execution-node lifecycle.

Requires ``X-Internal-API-Key`` scoped to infrastructure / execution nodes (or legacy ``INTERNAL_API_KEY``).
Intended for operators and automation — not end-user facing.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import and_
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from app.libs.db.database import get_db
from app.libs.observability.log_events import LogEvent, log_event
from app.libs.security.dependencies import require_internal_api_key
from app.libs.security.internal_auth import InternalApiScope
from app.services.audit_service.enums import AuditAction, AuditActorType, AuditOutcome
from app.services.audit_service.service import record_audit
from app.services.placement_service.bootstrap import default_local_node_key, ensure_default_local_execution_node
from app.services.placement_service.errors import ExecutionNodeNotFoundError
from app.services.placement_service.node_heartbeat import execution_node_heartbeat_age_seconds
from app.services.placement_service.models import ExecutionNode
from app.services.providers.errors import Ec2InvalidInstanceIdError, Ec2ProviderError

from app.services.workspace_service.models import Workspace, WorkspaceRuntime
from app.services.workspace_service.models.enums import WorkspaceStatus

from ...errors import Ec2ProvisionConfigurationError, NodeLifecycleError
from ...lifecycle import (
    deregister_node,
    mark_node_draining,
    promote_ec2_node_if_heartbeat_ready,
    provision_ec2_node,
    register_catalog_ec2_stub,
    register_existing_ec2_node,
    sync_node_state,
    terminate_ec2_node,
    undrain_node,
)
from ...execution_node_smoke import ExecutionNodeSmokeUnsupportedError, run_read_only_execution_node_smoke
from ...models import Ec2ProvisionRequest

from ..schemas import (
    ExecutionNodeCapacityResponse,
    ExecutionNodeSmokeReadOnlyBody,
    ExecutionNodeSmokeResponse,
    ExecutionNodeSummaryResponse,
    NodeKeyOrIdBody,
    NodeWorkspacesSummaryResponse,
    ProvisionExecutionNodeRequest,
    RegisterCatalogEc2Body,
    RegisterExistingEc2Body,
    SyncExecutionNodeBody,
    WorkspaceOnNodeBrief,
)

_logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/execution-nodes",
    tags=["internal-execution-nodes"],
    dependencies=[Depends(require_internal_api_key(InternalApiScope.INFRASTRUCTURE))],
)


def _audit_mutation(action: str, **fields: object) -> None:
    log_event(_logger, LogEvent.AUDIT_INTERNAL_EXECUTION_NODES_MUTATION, action=action, **fields)


def _select_kwargs(body: NodeKeyOrIdBody) -> dict:
    if body.node_id is not None:
        return {"node_id": body.node_id}
    return {"node_key": str(body.node_key).strip()}


class ExecutionNodeHeartbeatInBody(BaseModel):
    """JSON body for POST ``/heartbeat`` (execution node liveness)."""

    node_key: str = Field(..., min_length=1, max_length=128)
    docker_ok: bool = True
    disk_free_mb: int | None = None
    slots_in_use: int | None = None
    version: str | None = None


class ExecutionNodeHeartbeatOutBody(BaseModel):
    """JSON response for POST ``/heartbeat``."""

    id: int | None
    node_key: str
    status: str
    schedulable: bool
    last_heartbeat_at: datetime | None


def _merge_heartbeat_metadata_json(existing: dict[str, Any] | None, heartbeat_payload: dict[str, Any]) -> dict[str, Any]:
    """Merge ``heartbeat_payload`` under ``metadata_json['heartbeat']``."""
    base = dict(existing or {})
    hb = dict(base.get("heartbeat") or {})
    hb.update(heartbeat_payload)
    base["heartbeat"] = hb
    return base


def _load_execution_node_for_heartbeat(session: Session, node_key: str) -> ExecutionNode | None:
    key = node_key.strip()
    return session.exec(select(ExecutionNode).where(ExecutionNode.node_key == key)).first()


@router.get(
    "/",
    response_model=list[ExecutionNodeCapacityResponse],
    summary="List execution nodes with workspace slot capacity (debug / ops)",
)
def get_execution_nodes_with_capacity(session: Session = Depends(get_db)) -> list[ExecutionNodeCapacityResponse]:
    rows = list(session.exec(select(ExecutionNode).order_by(ExecutionNode.id.asc())).all())
    return [ExecutionNodeCapacityResponse.from_row_with_capacity(session, row) for row in rows]


@router.get(
    "/workspaces-by-node",
    response_model=list[NodeWorkspacesSummaryResponse],
    summary="Workspaces grouped by workspace_runtime.node_id (ops inventory)",
)
def get_workspaces_by_runtime_node(
    session: Session = Depends(get_db),
    limit_per_node: int = Query(
        50,
        ge=1,
        le=200,
        description="Maximum workspaces returned per node_key bucket (total count is always exact).",
    ),
) -> list[NodeWorkspacesSummaryResponse]:
    nodes = list(session.exec(select(ExecutionNode).order_by(ExecutionNode.node_key.asc())).all())
    stmt = (
        select(Workspace.workspace_id, Workspace.name, Workspace.status, WorkspaceRuntime.node_id)
        .join(WorkspaceRuntime, WorkspaceRuntime.workspace_id == Workspace.workspace_id)
        .where(
            Workspace.status != WorkspaceStatus.DELETED.value,
            and_(col(WorkspaceRuntime.node_id).is_not(None), WorkspaceRuntime.node_id != ""),
        )
    )
    by_key: defaultdict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for wid, name, st, nk in session.exec(stmt).all():
        key = str(nk or "").strip()
        if not key:
            continue
        by_key[key].append((int(wid), str(name or ""), str(st or "")))

    catalog_keys: set[str] = set()
    out: list[NodeWorkspacesSummaryResponse] = []
    for n in nodes:
        nk = (n.node_key or "").strip()
        if not nk:
            continue
        catalog_keys.add(nk)
        raw = by_key.get(nk, [])
        briefs = [
            WorkspaceOnNodeBrief(workspace_id=w, name=nm, status=st) for w, nm, st in raw[:limit_per_node]
        ]
        out.append(
            NodeWorkspacesSummaryResponse(
                node_key=nk,
                execution_node_id=n.id,
                workspace_count=len(raw),
                workspaces=briefs,
            ),
        )
    for nk in sorted(set(by_key.keys()) - catalog_keys):
        raw = by_key[nk]
        briefs = [
            WorkspaceOnNodeBrief(workspace_id=w, name=nm, status=st) for w, nm, st in raw[:limit_per_node]
        ]
        out.append(
            NodeWorkspacesSummaryResponse(
                node_key=nk,
                execution_node_id=None,
                workspace_count=len(raw),
                workspaces=briefs,
            ),
        )
    return out


@router.post(
    "/heartbeat",
    response_model=ExecutionNodeHeartbeatOutBody,
    summary="Record execution node heartbeat (POST /internal/execution-nodes/heartbeat)",
)
def post_execution_node_heartbeat(
    body: ExecutionNodeHeartbeatInBody,
    session: Session = Depends(get_db),
) -> ExecutionNodeHeartbeatOutBody:
    """Apply execution node heartbeat: update ``last_heartbeat_at`` and optional ``metadata_json['heartbeat']``.

    Does **not** modify ``schedulable`` or ``status`` (Phase 3b: catalog node-2 can heartbeat while
    ``schedulable=false``; placement still excludes it until undrain / explicit enable).
    """
    nk = str(body.node_key).strip()
    _logger.info(
        "execution_node_heartbeat_received",
        extra={"node_key": nk, "docker_ok": body.docker_ok},
    )
    node = _load_execution_node_for_heartbeat(session, nk)
    if node is None and nk == default_local_node_key().strip():
        ensure_default_local_execution_node(session)
        node = _load_execution_node_for_heartbeat(session, nk)
    if node is None:
        _logger.warning("execution_node_heartbeat_unknown_node", extra={"node_key": nk})
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"execution node key={nk!r} not found",
        )

    now = datetime.now(timezone.utc)
    node.last_heartbeat_at = now
    node.updated_at = now

    hb_payload: dict[str, Any] = {
        "received_at": now.isoformat(),
        "docker_ok": bool(body.docker_ok),
    }
    if body.disk_free_mb is not None:
        hb_payload["disk_free_mb"] = int(body.disk_free_mb)
    if body.slots_in_use is not None:
        hb_payload["slots_in_use"] = int(body.slots_in_use)
    if body.version is not None and str(body.version).strip():
        hb_payload["version"] = str(body.version).strip()[:128]

    node.metadata_json = _merge_heartbeat_metadata_json(node.metadata_json, hb_payload)
    if not body.docker_ok:
        node.last_error_code = "DOCKER_UNREACHABLE"
        node.last_error_message = "Heartbeat reported docker_ok=false"
    else:
        node.last_error_code = None
        node.last_error_message = None
    promote_ec2_node_if_heartbeat_ready(session, node, readiness="heartbeat")
    session.add(node)
    session.commit()
    session.refresh(node)

    log_event(
        _logger,
        LogEvent.EXECUTION_NODE_HEARTBEAT_RECORDED,
        node_key=nk,
        execution_node_id=node.id,
        heartbeat_age_seconds=execution_node_heartbeat_age_seconds(node),
        docker_ok=bool(body.docker_ok),
        disk_free_mb=body.disk_free_mb,
        slots_in_use=body.slots_in_use,
    )
    return ExecutionNodeHeartbeatOutBody(
        id=node.id,
        node_key=node.node_key,
        status=str(node.status or ""),
        schedulable=bool(node.schedulable),
        last_heartbeat_at=node.last_heartbeat_at,
    )


@router.post(
    "/smoke-read-only",
    response_model=ExecutionNodeSmokeResponse,
    summary="Run read-only docker info on an EC2 execution node (SSM or SSH smoke, Phase 3b)",
)
def post_execution_node_smoke_read_only(
    body: ExecutionNodeSmokeReadOnlyBody,
    session: Session = Depends(get_db),
) -> ExecutionNodeSmokeResponse:
    """Operator smoke: verify control plane can reach node Docker without changing ``schedulable`` or placement.

    Runs in the API process (same AWS/SSH credentials as other control-plane mutations). Phase 3b Step 6:
    use for catalog ``node-2`` while ``schedulable=false`` — no Traefik or workspace scheduling changes.
    """
    _audit_mutation("smoke_read_only", **_select_kwargs(body))
    try:
        payload = run_read_only_execution_node_smoke(
            session,
            read_only_command=body.read_only_command,
            **_select_kwargs(body),
        )
    except ExecutionNodeNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ExecutionNodeSmokeUnsupportedError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=e.message) from e
    return ExecutionNodeSmokeResponse.model_validate(payload)


def _merge_provision_request(body: ProvisionExecutionNodeRequest) -> Ec2ProvisionRequest:
    req = Ec2ProvisionRequest.from_settings()
    if body.ami_id is not None and str(body.ami_id).strip():
        req = replace(req, ami_id=str(body.ami_id).strip())
    if body.instance_type is not None and str(body.instance_type).strip():
        req = replace(req, instance_type=str(body.instance_type).strip())
    if body.subnet_id is not None and str(body.subnet_id).strip():
        req = replace(req, subnet_id=str(body.subnet_id).strip())
    if body.security_group_ids is not None:
        req = replace(req, security_group_ids=list(body.security_group_ids))
    if body.iam_instance_profile_name is not None:
        v = str(body.iam_instance_profile_name).strip()
        req = replace(req, iam_instance_profile_name=v or None)
    if body.key_name is not None:
        v = str(body.key_name).strip()
        req = replace(req, key_name=v or None)
    if body.region is not None:
        v = str(body.region).strip()
        req = replace(req, region=v or None)
    if body.node_key is not None:
        v = str(body.node_key).strip()
        req = replace(req, node_key=v or None)
    if body.name_tag is not None:
        v = str(body.name_tag).strip()
        req = replace(req, name_tag=v or None)
    if body.execution_mode is not None:
        v = str(body.execution_mode).strip()
        req = replace(req, execution_mode=v or None)
    if body.ssh_user is not None:
        v = str(body.ssh_user).strip()
        req = replace(req, ssh_user=v or None)
    if body.extra_tags:
        req = replace(req, extra_tags=dict(body.extra_tags))
    return req


@router.post(
    "/provision",
    response_model=ExecutionNodeSummaryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision one EC2 instance and create a PROVISIONING execution node",
)
def post_provision_execution_node(
    body: ProvisionExecutionNodeRequest = Body(default_factory=ProvisionExecutionNodeRequest),
    session: Session = Depends(get_db),
) -> ExecutionNodeSummaryResponse:
    _audit_mutation("provision")
    b = body
    try:
        req = _merge_provision_request(b)
        node = provision_ec2_node(
            session,
            req,
            wait_until_running=b.wait_until_running,
        )
        session.commit()
        session.refresh(node)
    except Ec2ProvisionConfigurationError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Ec2ProviderError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    except NodeLifecycleError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    return ExecutionNodeSummaryResponse.from_row(node)


@router.post(
    "/register-catalog-ec2",
    response_model=ExecutionNodeSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Phase 3b Step 4: upsert EC2 catalog row without AWS (schedulable=false; optional placeholder instance id)",
)
def post_register_catalog_ec2(
    body: RegisterCatalogEc2Body,
    session: Session = Depends(get_db),
) -> ExecutionNodeSummaryResponse:
    _audit_mutation("register_catalog_ec2", node_key=body.node_key.strip())
    try:
        node = register_catalog_ec2_stub(
            session,
            node_key=body.node_key.strip(),
            name=body.name,
            provider_instance_id=body.provider_instance_id,
            private_ip=body.private_ip,
            public_ip=body.public_ip,
            region=body.region,
            availability_zone=body.availability_zone,
            instance_type=body.instance_type,
            execution_mode=body.execution_mode,
            ssh_user=body.ssh_user,
            status=body.status,
            total_cpu=body.total_cpu,
            total_memory_mb=body.total_memory_mb,
            allocatable_cpu=body.allocatable_cpu,
            allocatable_memory_mb=body.allocatable_memory_mb,
            max_workspaces=body.max_workspaces,
            allocatable_disk_mb=body.allocatable_disk_mb,
            align_status_with_heartbeat=bool(body.align_status_with_heartbeat),
        )
        record_audit(
            session,
            action=AuditAction.NODE_REGISTERED.value,
            resource_type="node",
            resource_id=node.node_key,
            actor_type=AuditActorType.INTERNAL_SERVICE.value,
            outcome=AuditOutcome.SUCCESS.value,
            node_id=node.node_key,
            metadata={"register_catalog_ec2": True, "provider_instance_id": node.provider_instance_id},
        )
        session.commit()
        session.refresh(node)
    except NodeLifecycleError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    return ExecutionNodeSummaryResponse.from_row(node)


@router.post(
    "/register-existing",
    response_model=ExecutionNodeSummaryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a pre-existing EC2 instance (optional catalog_only forces schedulable=false, Step 4)",
)
def post_register_existing_ec2(
    body: RegisterExistingEc2Body,
    session: Session = Depends(get_db),
) -> ExecutionNodeSummaryResponse:
    _audit_mutation(
        "register_existing",
        instance_id=body.instance_id.strip(),
        catalog_only=bool(body.catalog_only),
    )
    try:
        node = register_existing_ec2_node(
            session,
            body.instance_id.strip(),
            node_key=body.node_key,
            ssh_user=body.ssh_user,
            execution_mode=body.execution_mode,
            catalog_only=bool(body.catalog_only),
        )
        record_audit(
            session,
            action=AuditAction.NODE_REGISTERED.value,
            resource_type="node",
            resource_id=node.node_key,
            actor_type=AuditActorType.INTERNAL_SERVICE.value,
            outcome=AuditOutcome.SUCCESS.value,
            node_id=node.node_key,
            metadata={"instance_id": body.instance_id.strip(), "execution_mode": body.execution_mode},
        )
        session.commit()
        session.refresh(node)
    except Ec2InvalidInstanceIdError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Ec2ProviderError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return ExecutionNodeSummaryResponse.from_row(node)


@router.post(
    "/sync",
    response_model=ExecutionNodeSummaryResponse,
    summary="Refresh EC2 fields and optionally promote PROVISIONING → READY (SSM check for ssm_docker)",
)
def post_sync_execution_node(
    body: SyncExecutionNodeBody,
    session: Session = Depends(get_db),
) -> ExecutionNodeSummaryResponse:
    _audit_mutation("sync", node_id=body.node_id, node_key=body.node_key)
    try:
        node = sync_node_state(
            session,
            promote_provisioning_when_ready=body.promote_provisioning_when_ready,
            **_select_kwargs(body),
        )
        session.commit()
        session.refresh(node)
    except ExecutionNodeNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except NodeLifecycleError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Ec2ProviderError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return ExecutionNodeSummaryResponse.from_row(node)


@router.post(
    "/drain",
    response_model=ExecutionNodeSummaryResponse,
    summary="Mark node DRAINING and not schedulable",
)
def post_drain_execution_node(
    body: NodeKeyOrIdBody,
    session: Session = Depends(get_db),
) -> ExecutionNodeSummaryResponse:
    _audit_mutation("drain", node_id=body.node_id, node_key=body.node_key)
    try:
        node = mark_node_draining(session, **_select_kwargs(body))
        session.commit()
        session.refresh(node)
    except ExecutionNodeNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return ExecutionNodeSummaryResponse.from_row(node)


@router.post(
    "/undrain",
    response_model=ExecutionNodeSummaryResponse,
    summary="Re-admit a DRAINING node (READY + schedulable) or set schedulable=true on READY",
)
def post_undrain_execution_node(
    body: NodeKeyOrIdBody,
    session: Session = Depends(get_db),
) -> ExecutionNodeSummaryResponse:
    _audit_mutation("undrain", node_id=body.node_id, node_key=body.node_key)
    try:
        node = undrain_node(session, **_select_kwargs(body))
        session.commit()
        session.refresh(node)
    except ExecutionNodeNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except NodeLifecycleError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    return ExecutionNodeSummaryResponse.from_row(node)


@router.post(
    "/deregister",
    response_model=ExecutionNodeSummaryResponse,
    summary="Soft deregister: TERMINATED + not schedulable (does not stop EC2)",
)
def post_deregister_execution_node(
    body: NodeKeyOrIdBody,
    session: Session = Depends(get_db),
) -> ExecutionNodeSummaryResponse:
    _audit_mutation("deregister", node_id=body.node_id, node_key=body.node_key)
    try:
        node = deregister_node(session, **_select_kwargs(body))
        record_audit(
            session,
            action=AuditAction.NODE_DEREGISTERED.value,
            resource_type="node",
            resource_id=node.node_key,
            actor_type=AuditActorType.INTERNAL_SERVICE.value,
            outcome=AuditOutcome.SUCCESS.value,
            node_id=node.node_key,
        )
        session.commit()
        session.refresh(node)
    except ExecutionNodeNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return ExecutionNodeSummaryResponse.from_row(node)


@router.post(
    "/terminate",
    response_model=ExecutionNodeSummaryResponse,
    summary="EC2 only: terminate_instances in AWS and move through TERMINATING → TERMINATED",
)
def post_terminate_execution_node(
    body: NodeKeyOrIdBody,
    session: Session = Depends(get_db),
) -> ExecutionNodeSummaryResponse:
    _audit_mutation("terminate", node_id=body.node_id, node_key=body.node_key)
    try:
        node = terminate_ec2_node(session, **_select_kwargs(body))
        record_audit(
            session,
            action=AuditAction.NODE_TERMINATED.value,
            resource_type="node",
            resource_id=node.node_key,
            actor_type=AuditActorType.INTERNAL_SERVICE.value,
            outcome=AuditOutcome.SUCCESS.value,
            node_id=node.node_key,
            metadata={"instance_id": node.provider_instance_id or None},
        )
        session.commit()
        session.refresh(node)
    except ExecutionNodeNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except NodeLifecycleError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Ec2ProviderError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return ExecutionNodeSummaryResponse.from_row(node)
