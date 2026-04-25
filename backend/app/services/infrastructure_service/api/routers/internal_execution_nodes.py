"""
Internal admin routes: EC2 provisioning and execution-node lifecycle.

Requires ``X-Internal-API-Key`` scoped to infrastructure / execution nodes (or legacy ``INTERNAL_API_KEY``).
Intended for operators and automation — not end-user facing.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlmodel import Session, select

from app.libs.db.database import get_db
from app.libs.observability.log_events import LogEvent, log_event
from app.libs.security.dependencies import require_internal_api_key
from app.libs.security.internal_auth import InternalApiScope
from app.services.audit_service.enums import AuditAction, AuditActorType, AuditOutcome
from app.services.audit_service.service import record_audit
from app.services.placement_service.errors import ExecutionNodeNotFoundError
from app.services.placement_service.models import ExecutionNode
from app.services.providers.errors import Ec2InvalidInstanceIdError, Ec2ProviderError

from ...errors import Ec2ProvisionConfigurationError, NodeLifecycleError
from ...lifecycle import (
    deregister_node,
    mark_node_draining,
    provision_ec2_node,
    register_existing_ec2_node,
    sync_node_state,
    terminate_ec2_node,
)
from ...models import Ec2ProvisionRequest
from ..schemas import (
    ExecutionNodeCapacityResponse,
    ExecutionNodeSummaryResponse,
    NodeKeyOrIdBody,
    ProvisionExecutionNodeRequest,
    RegisterExistingEc2Body,
    SyncExecutionNodeBody,
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


@router.get(
    "/",
    response_model=list[ExecutionNodeCapacityResponse],
    summary="List execution nodes with workspace slot capacity (debug / ops)",
)
def get_execution_nodes_with_capacity(session: Session = Depends(get_db)) -> list[ExecutionNodeCapacityResponse]:
    rows = list(session.exec(select(ExecutionNode).order_by(ExecutionNode.id.asc())).all())
    return [ExecutionNodeCapacityResponse.from_row_with_capacity(session, row) for row in rows]


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
    "/register-existing",
    response_model=ExecutionNodeSummaryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a pre-existing EC2 instance (immediate READY if running, unless lifecycle state blocks)",
)
def post_register_existing_ec2(
    body: RegisterExistingEc2Body,
    session: Session = Depends(get_db),
) -> ExecutionNodeSummaryResponse:
    _audit_mutation("register_existing", instance_id=body.instance_id.strip())
    try:
        node = register_existing_ec2_node(
            session,
            body.instance_id.strip(),
            node_key=body.node_key,
            ssh_user=body.ssh_user,
            execution_mode=body.execution_mode,
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
