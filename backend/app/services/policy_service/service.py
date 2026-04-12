"""Policy evaluation logic.

Each ``evaluate_*`` function:
  1. Loads applicable active policies (GLOBAL first, then scope-specific).
  2. Checks each policy's rules_json for a blocking rule.
  3. On denial: writes a POLICY_DENIED audit row and commits it durably, then raises
     ``PolicyViolationError`` — so the denial is recorded even when the caller rolls back.
  4. Returns None on success (no audit row; callers may record POLICY_EVALUATED if needed).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import and_, false, or_
from sqlmodel import Session, select

from app.libs.observability.correlation import get_correlation_id
from app.services.audit_service.enums import AuditAction, AuditActorType, AuditOutcome
from app.services.audit_service.service import record_audit

from .enums import ScopeType
from .errors import PolicyViolationError
from .models import Policy

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_applicable_policies(
    session: Session,
    *,
    owner_user_id: int | None = None,
    workspace_id: int | None = None,
) -> list[Policy]:
    """Return all active policies applicable to this context.

    Precedence: GLOBAL policies + user-scoped (if owner_user_id) + workspace-scoped
    (if workspace_id), ordered by creation date so oldest wins on conflict.
    """
    filters = [Policy.scope_type == ScopeType.GLOBAL.value]
    if owner_user_id is not None:
        filters.append(
            and_(
                Policy.scope_type == ScopeType.USER.value,
                Policy.scope_id == owner_user_id,
            )
        )
    if workspace_id is not None:
        filters.append(
            and_(
                Policy.scope_type == ScopeType.WORKSPACE.value,
                Policy.scope_id == workspace_id,
            )
        )
    stmt = (
        select(Policy)
        .where(Policy.is_active == True)  # noqa: E712
        .where(or_(*filters) if len(filters) > 1 else filters[0])
        .order_by(Policy.created_at)
    )
    return list(session.exec(stmt).all())


def _deny_and_raise(
    session: Session,
    *,
    policy: Policy,
    action: str,
    reason: str,
    owner_user_id: int | None = None,
    workspace_id: int | None = None,
    correlation_id: str | None = None,
) -> None:
    """Record a POLICY_DENIED audit row, commit it, then raise PolicyViolationError.

    The commit ensures the denial is durable even when the caller rolls back.
    Audit failures are swallowed so they never mask the PolicyViolationError.
    """
    cid = correlation_id or get_correlation_id()
    try:
        record_audit(
            session,
            action=AuditAction.POLICY_DENIED.value,
            resource_type="policy",
            resource_id=policy.policy_id,
            actor_user_id=owner_user_id,
            actor_type=AuditActorType.USER.value if owner_user_id else AuditActorType.SYSTEM.value,
            outcome=AuditOutcome.DENIED.value,
            workspace_id=workspace_id,
            correlation_id=cid,
            reason=reason[:4096],
            metadata={
                "policy_name": policy.name,
                "policy_type": policy.policy_type,
                "attempted_action": action,
            },
        )
        session.commit()
    except Exception:
        logger.warning("policy_deny_audit_commit_failed", exc_info=True)
        try:
            session.rollback()
        except Exception:
            pass
    raise PolicyViolationError(
        policy_name=policy.name,
        action=action,
        reason=reason,
    )


def _rule(rules: dict[str, Any], key: str, default: Any) -> Any:
    """Safely read a rule key with a typed default."""
    val = rules.get(key)
    return val if val is not None else default


# ---------------------------------------------------------------------------
# Evaluation entry points
# ---------------------------------------------------------------------------

def evaluate_workspace_creation(
    session: Session,
    *,
    owner_user_id: int,
    image: str | None = None,
    is_private: bool = True,
    correlation_id: str | None = None,
) -> None:
    """Raise PolicyViolationError if any policy blocks workspace creation."""
    policies = _load_applicable_policies(session, owner_user_id=owner_user_id)
    for policy in policies:
        rules = policy.rules_json or {}
        if not _rule(rules, "allow_workspace_creation", True):
            _deny_and_raise(
                session,
                policy=policy,
                action="workspace.create",
                reason="Workspace creation is disabled by policy",
                owner_user_id=owner_user_id,
                correlation_id=correlation_id,
            )
        if image and _rule(rules, "allowed_runtime_images", None) is not None:
            allowed: list[str] = rules["allowed_runtime_images"]
            if isinstance(allowed, list) and image not in allowed:
                _deny_and_raise(
                    session,
                    policy=policy,
                    action="workspace.create",
                    reason=f"Runtime image '{image}' is not in the allowed list",
                    owner_user_id=owner_user_id,
                    correlation_id=correlation_id,
                )
        if not is_private and _rule(rules, "require_private_workspaces", False):
            _deny_and_raise(
                session,
                policy=policy,
                action="workspace.create",
                reason="Policy requires all workspaces to be private",
                owner_user_id=owner_user_id,
                correlation_id=correlation_id,
            )


def evaluate_workspace_start(
    session: Session,
    *,
    owner_user_id: int,
    workspace_id: int,
    correlation_id: str | None = None,
) -> None:
    """Raise PolicyViolationError if any policy blocks workspace start/restart."""
    policies = _load_applicable_policies(
        session, owner_user_id=owner_user_id, workspace_id=workspace_id
    )
    for policy in policies:
        rules = policy.rules_json or {}
        if not _rule(rules, "allow_workspace_start", True):
            _deny_and_raise(
                session,
                policy=policy,
                action="workspace.start",
                reason="Workspace start is disabled by policy",
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )


def evaluate_snapshot_creation(
    session: Session,
    *,
    owner_user_id: int,
    workspace_id: int,
    correlation_id: str | None = None,
) -> None:
    """Raise PolicyViolationError if any policy blocks snapshot creation."""
    policies = _load_applicable_policies(
        session, owner_user_id=owner_user_id, workspace_id=workspace_id
    )
    for policy in policies:
        rules = policy.rules_json or {}
        if not _rule(rules, "allow_snapshot_creation", True):
            _deny_and_raise(
                session,
                policy=policy,
                action="workspace.snapshot.create",
                reason="Snapshot creation is disabled by policy",
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )


def evaluate_session_creation(
    session: Session,
    *,
    owner_user_id: int,
    workspace_id: int,
    correlation_id: str | None = None,
) -> None:
    """Raise PolicyViolationError if any policy blocks session/attach creation."""
    policies = _load_applicable_policies(
        session, owner_user_id=owner_user_id, workspace_id=workspace_id
    )
    for policy in policies:
        rules = policy.rules_json or {}
        if not _rule(rules, "allow_session_creation", True):
            _deny_and_raise(
                session,
                policy=policy,
                action="workspace.session.create",
                reason="Session creation is disabled by policy",
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )


def evaluate_node_provisioning(
    session: Session,
    *,
    correlation_id: str | None = None,
) -> None:
    """Raise PolicyViolationError if any global policy blocks node provisioning."""
    policies = _load_applicable_policies(session)
    for policy in policies:
        rules = policy.rules_json or {}
        if not _rule(rules, "allow_node_provisioning", True):
            _deny_and_raise(
                session,
                policy=policy,
                action="node.provision",
                reason="Node provisioning is disabled by policy",
                correlation_id=correlation_id,
            )
