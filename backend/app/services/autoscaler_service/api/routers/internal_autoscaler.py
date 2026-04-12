"""
Internal admin routes: autoscaler evaluate / provision / reclaim.

Requires ``X-Internal-API-Key`` scoped to autoscaler (or legacy ``INTERNAL_API_KEY``). Does not replace explicit EC2 lifecycle routes under
``/internal/execution-nodes`` — this is a thin orchestration layer for fleet ops.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.libs.db.database import get_db
from app.libs.observability.log_events import LogEvent, log_event
from app.libs.security.dependencies import require_internal_api_key
from app.libs.security.internal_auth import InternalApiScope
from app.services.audit_service.enums import AuditAction, AuditActorType, AuditOutcome
from app.services.audit_service.service import record_audit
from app.services.usage_service.enums import UsageEventType
from app.services.usage_service.service import record_usage

from ...models import ScaleDownEvaluation, ScaleUpEvaluation
from ...service import (
    evaluate_scale_down,
    evaluate_scale_up,
    provision_capacity_if_needed,
    reclaim_one_idle_ec2_node,
)
from ..schemas import (
    AutoscalerEvaluateResponse,
    ProvisionOneResponse,
    ReclaimOneResponse,
    ScaleDownEvaluationResponse,
    ScaleUpEvaluationResponse,
)

_logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/autoscaler",
    tags=["internal-autoscaler"],
    dependencies=[Depends(require_internal_api_key(InternalApiScope.AUTOSCALER))],
)


def _up(ev: ScaleUpEvaluation) -> ScaleUpEvaluationResponse:
    return ScaleUpEvaluationResponse(
        should_provision=ev.should_provision,
        reason=ev.reason,
        provisioning_in_flight=ev.provisioning_in_flight,
    )


def _down(ev: ScaleDownEvaluation) -> ScaleDownEvaluationResponse:
    return ScaleDownEvaluationResponse(
        node_key=ev.node_key,
        reason=ev.reason,
        idle_ec2_ready_nodes=ev.idle_ec2_ready_nodes,
    )


@router.get(
    "/evaluate",
    response_model=AutoscalerEvaluateResponse,
    summary="Dry-run scale-up and scale-down decisions",
)
def get_autoscaler_evaluate(session: Session = Depends(get_db)) -> AutoscalerEvaluateResponse:
    up = evaluate_scale_up(session, insufficient_capacity=True)
    down = evaluate_scale_down(session)
    return AutoscalerEvaluateResponse(scale_up=_up(up), scale_down=_down(down))


@router.post(
    "/provision-one",
    response_model=ProvisionOneResponse,
    summary="Provision one EC2 node if scale-up evaluation allows",
)
def post_autoscaler_provision_one(session: Session = Depends(get_db)) -> ProvisionOneResponse:
    log_event(_logger, LogEvent.AUDIT_INTERNAL_AUTOSCALER_PROVISION_ONE)
    ev = evaluate_scale_up(session, insufficient_capacity=True)
    if not ev.should_provision:
        return ProvisionOneResponse(
            provisioned=False,
            evaluation=_up(ev),
            node_key=None,
            instance_id=None,
        )
    try:
        node = provision_capacity_if_needed(session, ev)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        ) from e
    record_audit(
        session,
        action=AuditAction.AUTOSCALER_SCALE_UP.value,
        resource_type="node",
        resource_id=(node.node_key if node else None),
        actor_type=AuditActorType.INTERNAL_SERVICE.value,
        outcome=AuditOutcome.SUCCESS.value,
        node_id=(node.node_key if node else None),
        metadata={"instance_id": (node.provider_instance_id or "") if node else None},
    )
    record_usage(
        session,
        event_type=UsageEventType.AUTOSCALER_SCALE_UP.value,
        node_id=(node.node_key if node else None),
    )
    record_usage(
        session,
        event_type=UsageEventType.NODE_PROVISIONED.value,
        node_id=(node.node_key if node else None),
    )
    session.commit()
    iid = (node.provider_instance_id or "").strip() if node else ""
    return ProvisionOneResponse(
        provisioned=True,
        evaluation=_up(ev),
        node_key=node.node_key if node else None,
        instance_id=iid or None,
    )


@router.post(
    "/reclaim-one-idle",
    response_model=ReclaimOneResponse,
    summary="Drain and terminate one idle EC2 node (destructive; conservative policy)",
)
def post_autoscaler_reclaim_one_idle(session: Session = Depends(get_db)) -> ReclaimOneResponse:
    log_event(_logger, LogEvent.AUDIT_INTERNAL_AUTOSCALER_RECLAIM_ONE)
    try:
        node = reclaim_one_idle_ec2_node(session)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        ) from e
    if node is None:
        down = evaluate_scale_down(session)
        return ReclaimOneResponse(reclaimed=False, node_key=None, reason=down.reason)
    record_audit(
        session,
        action=AuditAction.AUTOSCALER_SCALE_DOWN.value,
        resource_type="node",
        resource_id=node.node_key,
        actor_type=AuditActorType.INTERNAL_SERVICE.value,
        outcome=AuditOutcome.SUCCESS.value,
        node_id=node.node_key,
    )
    record_usage(
        session,
        event_type=UsageEventType.AUTOSCALER_SCALE_DOWN.value,
        node_id=node.node_key,
    )
    record_usage(
        session,
        event_type=UsageEventType.NODE_TERMINATED.value,
        node_id=node.node_key,
    )
    session.commit()
    return ReclaimOneResponse(reclaimed=True, node_key=node.node_key, reason=None)
