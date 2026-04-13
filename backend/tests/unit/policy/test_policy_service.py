"""Unit tests for policy evaluation logic (SQLite in-memory)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.services.auth_service.models import UserAuth
from app.services.policy_service.enums import PolicyType, ScopeType
from app.services.policy_service.errors import PolicyViolationError
from app.services.policy_service.models import Policy
from app.services.policy_service.service import (
    evaluate_node_provisioning,
    evaluate_session_creation,
    evaluate_snapshot_creation,
    evaluate_workspace_creation,
    evaluate_workspace_start,
)
from app.services.workspace_service.models import Workspace


@pytest.fixture()
def policy_session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _seed_user(session: Session) -> int:
    u = UserAuth(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@t.dev",
        password_hash="x",
        created_at=datetime.now(timezone.utc),
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u.user_auth_id  # type: ignore[return-value]


def _seed_workspace(session: Session, owner: int) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="pol-ws",
        description="",
        owner_user_id=owner,
        status="STOPPED",
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws.workspace_id  # type: ignore[return-value]


def _add_policy(
    session: Session,
    *,
    rules: dict,
    scope_type: ScopeType = ScopeType.GLOBAL,
    scope_id: int | None = None,
    name: str | None = None,
) -> Policy:
    now = datetime.now(timezone.utc)
    p = Policy(
        name=name or f"pol_{uuid.uuid4().hex[:6]}",
        policy_type=PolicyType.SYSTEM.value,
        scope_type=scope_type.value,
        scope_id=scope_id,
        rules_json=rules,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(p)
    session.commit()
    return p


# ---------------------------------------------------------------------------
# evaluate_workspace_creation
# ---------------------------------------------------------------------------

class TestEvaluateWorkspaceCreation:
    def test_no_policies_allows(self, policy_session: Session) -> None:
        uid = _seed_user(policy_session)
        evaluate_workspace_creation(policy_session, owner_user_id=uid)

    def test_deny_workspace_creation_raises(self, policy_session: Session) -> None:
        uid = _seed_user(policy_session)
        _add_policy(policy_session, rules={"allow_workspace_creation": False})
        with pytest.raises(PolicyViolationError) as exc_info:
            evaluate_workspace_creation(policy_session, owner_user_id=uid)
        assert exc_info.value.action == "workspace.create"

    def test_allow_workspace_creation_passes(self, policy_session: Session) -> None:
        uid = _seed_user(policy_session)
        _add_policy(policy_session, rules={"allow_workspace_creation": True})
        evaluate_workspace_creation(policy_session, owner_user_id=uid)

    def test_allowed_images_blocks_unlisted_image(self, policy_session: Session) -> None:
        uid = _seed_user(policy_session)
        _add_policy(policy_session, rules={"allowed_runtime_images": ["nginx:alpine"]})
        with pytest.raises(PolicyViolationError) as exc_info:
            evaluate_workspace_creation(policy_session, owner_user_id=uid, image="ubuntu:22.04")
        assert "not in the allowed list" in exc_info.value.reason

    def test_allowed_images_permits_listed_image(self, policy_session: Session) -> None:
        uid = _seed_user(policy_session)
        _add_policy(policy_session, rules={"allowed_runtime_images": ["nginx:alpine", "ubuntu:22.04"]})
        evaluate_workspace_creation(policy_session, owner_user_id=uid, image="nginx:alpine")

    def test_null_allowed_images_permits_any_image(self, policy_session: Session) -> None:
        uid = _seed_user(policy_session)
        _add_policy(policy_session, rules={"allowed_runtime_images": None})
        evaluate_workspace_creation(policy_session, owner_user_id=uid, image="anything:latest")

    def test_require_private_blocks_public_workspace(self, policy_session: Session) -> None:
        uid = _seed_user(policy_session)
        _add_policy(policy_session, rules={"require_private_workspaces": True})
        with pytest.raises(PolicyViolationError) as exc_info:
            evaluate_workspace_creation(policy_session, owner_user_id=uid, is_private=False)
        assert "private" in exc_info.value.reason.lower()

    def test_inactive_policy_is_ignored(self, policy_session: Session) -> None:
        uid = _seed_user(policy_session)
        now = datetime.now(timezone.utc)
        p = Policy(
            name="inactive_pol",
            policy_type=PolicyType.SYSTEM.value,
            scope_type=ScopeType.GLOBAL.value,
            rules_json={"allow_workspace_creation": False},
            is_active=False,
            created_at=now,
            updated_at=now,
        )
        policy_session.add(p)
        policy_session.commit()
        evaluate_workspace_creation(policy_session, owner_user_id=uid)

    def test_user_scoped_policy_applies_to_matching_user(self, policy_session: Session) -> None:
        uid = _seed_user(policy_session)
        uid2 = _seed_user(policy_session)
        _add_policy(
            policy_session,
            scope_type=ScopeType.USER,
            scope_id=uid,
            rules={"allow_workspace_creation": False},
        )
        with pytest.raises(PolicyViolationError):
            evaluate_workspace_creation(policy_session, owner_user_id=uid)
        evaluate_workspace_creation(policy_session, owner_user_id=uid2)

    def test_denial_records_audit_row(self, policy_session: Session) -> None:
        from app.services.audit_service.models import AuditLog
        from sqlmodel import select as sel

        uid = _seed_user(policy_session)
        _add_policy(policy_session, rules={"allow_workspace_creation": False})
        with pytest.raises(PolicyViolationError):
            evaluate_workspace_creation(policy_session, owner_user_id=uid)

        rows = policy_session.exec(
            sel(AuditLog).where(AuditLog.action == "policy.denied")
        ).all()
        assert len(rows) == 1
        assert rows[0].outcome == "denied"


# ---------------------------------------------------------------------------
# evaluate_workspace_start
# ---------------------------------------------------------------------------

class TestEvaluateWorkspaceStart:
    def test_no_policies_allows(self, policy_session: Session) -> None:
        uid = _seed_user(policy_session)
        wid = _seed_workspace(policy_session, uid)
        evaluate_workspace_start(policy_session, owner_user_id=uid, workspace_id=wid)

    def test_deny_start_raises(self, policy_session: Session) -> None:
        uid = _seed_user(policy_session)
        wid = _seed_workspace(policy_session, uid)
        _add_policy(policy_session, rules={"allow_workspace_start": False})
        with pytest.raises(PolicyViolationError) as exc_info:
            evaluate_workspace_start(policy_session, owner_user_id=uid, workspace_id=wid)
        assert exc_info.value.action == "workspace.start"


# ---------------------------------------------------------------------------
# evaluate_snapshot_creation
# ---------------------------------------------------------------------------

class TestEvaluateSnapshotCreation:
    def test_no_policies_allows(self, policy_session: Session) -> None:
        uid = _seed_user(policy_session)
        wid = _seed_workspace(policy_session, uid)
        evaluate_snapshot_creation(policy_session, owner_user_id=uid, workspace_id=wid)

    def test_deny_snapshot_raises(self, policy_session: Session) -> None:
        uid = _seed_user(policy_session)
        wid = _seed_workspace(policy_session, uid)
        _add_policy(policy_session, rules={"allow_snapshot_creation": False})
        with pytest.raises(PolicyViolationError):
            evaluate_snapshot_creation(policy_session, owner_user_id=uid, workspace_id=wid)


# ---------------------------------------------------------------------------
# evaluate_session_creation
# ---------------------------------------------------------------------------

class TestEvaluateSessionCreation:
    def test_no_policies_allows(self, policy_session: Session) -> None:
        uid = _seed_user(policy_session)
        wid = _seed_workspace(policy_session, uid)
        evaluate_session_creation(policy_session, owner_user_id=uid, workspace_id=wid)

    def test_deny_session_raises(self, policy_session: Session) -> None:
        uid = _seed_user(policy_session)
        wid = _seed_workspace(policy_session, uid)
        _add_policy(policy_session, rules={"allow_session_creation": False})
        with pytest.raises(PolicyViolationError):
            evaluate_session_creation(policy_session, owner_user_id=uid, workspace_id=wid)


# ---------------------------------------------------------------------------
# evaluate_node_provisioning
# ---------------------------------------------------------------------------

class TestEvaluateNodeProvisioning:
    def test_no_policies_allows(self, policy_session: Session) -> None:
        evaluate_node_provisioning(policy_session)

    def test_deny_provisioning_raises(self, policy_session: Session) -> None:
        _add_policy(policy_session, rules={"allow_node_provisioning": False})
        with pytest.raises(PolicyViolationError) as exc_info:
            evaluate_node_provisioning(policy_session)
        assert exc_info.value.action == "node.provision"
