"""Unit tests: WorkspaceUsageRecord creation, aggregation, and system-level events (SQLite)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.services.auth_service.models import UserAuth
from app.services.usage_service.enums import UsageEventType
from app.services.usage_service.models import WorkspaceUsageRecord
from app.services.usage_service.service import (
    get_user_usage_summary,
    get_workspace_usage_summary,
    record_usage,
)
from app.services.workspace_service.models import Workspace


@pytest.fixture()
def usage_session() -> Session:
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
    assert u.user_auth_id is not None
    return u.user_auth_id


def _seed_workspace(session: Session, owner: int) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="usg-ws",
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
    assert ws.workspace_id is not None
    return ws.workspace_id


class TestRecordUsage:
    def test_creates_row(self, usage_session: Session) -> None:
        uid = _seed_user(usage_session)
        wid = _seed_workspace(usage_session, uid)
        row = record_usage(
            usage_session,
            workspace_id=wid,
            owner_user_id=uid,
            event_type=UsageEventType.WORKSPACE_STARTED.value,
        )
        usage_session.commit()
        assert row.usage_record_id is not None
        assert row.workspace_id == wid
        assert row.owner_user_id == uid
        assert row.event_type == "workspace.started"
        assert row.quantity == 1

    def test_custom_quantity(self, usage_session: Session) -> None:
        uid = _seed_user(usage_session)
        wid = _seed_workspace(usage_session, uid)
        row = record_usage(
            usage_session,
            workspace_id=wid,
            owner_user_id=uid,
            event_type=UsageEventType.SNAPSHOT_CREATED.value,
            quantity=204800,
        )
        usage_session.commit()
        assert row.quantity == 204800

    def test_system_event_without_workspace(self, usage_session: Session) -> None:
        """Node-level events (autoscaler) may omit workspace_id and owner_user_id."""
        row = record_usage(
            usage_session,
            event_type=UsageEventType.NODE_PROVISIONED.value,
            node_id="node-abc",
        )
        usage_session.commit()
        assert row.workspace_id is None
        assert row.owner_user_id is None
        assert row.node_id == "node-abc"

    def test_negative_quantity_clamped_to_zero(self, usage_session: Session) -> None:
        uid = _seed_user(usage_session)
        wid = _seed_workspace(usage_session, uid)
        row = record_usage(
            usage_session,
            workspace_id=wid,
            owner_user_id=uid,
            event_type=UsageEventType.WORKSPACE_STOPPED.value,
            quantity=-999,
        )
        usage_session.commit()
        assert row.quantity == 0


class TestUsageSummaries:
    def test_workspace_summary_aggregates_quantities(self, usage_session: Session) -> None:
        uid = _seed_user(usage_session)
        wid = _seed_workspace(usage_session, uid)

        record_usage(usage_session, workspace_id=wid, owner_user_id=uid, event_type=UsageEventType.WORKSPACE_STARTED.value)
        record_usage(usage_session, workspace_id=wid, owner_user_id=uid, event_type=UsageEventType.WORKSPACE_STARTED.value)
        record_usage(usage_session, workspace_id=wid, owner_user_id=uid, event_type=UsageEventType.WORKSPACE_STOPPED.value)
        record_usage(usage_session, workspace_id=wid, owner_user_id=uid, event_type=UsageEventType.SNAPSHOT_CREATED.value, quantity=5000)
        usage_session.commit()

        summary = get_workspace_usage_summary(usage_session, workspace_id=wid)
        assert summary.workspace_id == wid
        assert summary.totals[UsageEventType.WORKSPACE_STARTED.value] == 2
        assert summary.totals[UsageEventType.WORKSPACE_STOPPED.value] == 1
        assert summary.totals[UsageEventType.SNAPSHOT_CREATED.value] == 5000

    def test_user_summary_across_workspaces(self, usage_session: Session) -> None:
        uid = _seed_user(usage_session)
        wid1 = _seed_workspace(usage_session, uid)
        wid2 = _seed_workspace(usage_session, uid)

        record_usage(usage_session, workspace_id=wid1, owner_user_id=uid, event_type=UsageEventType.WORKSPACE_STARTED.value)
        record_usage(usage_session, workspace_id=wid2, owner_user_id=uid, event_type=UsageEventType.WORKSPACE_STARTED.value)
        record_usage(usage_session, workspace_id=wid1, owner_user_id=uid, event_type=UsageEventType.SESSION_CREATED.value)
        usage_session.commit()

        summary = get_user_usage_summary(usage_session, owner_user_id=uid)
        assert summary.owner_user_id == uid
        assert summary.totals_by_event[UsageEventType.WORKSPACE_STARTED.value] == 2
        assert summary.totals_by_event[UsageEventType.SESSION_CREATED.value] == 1

    def test_workspace_summary_empty(self, usage_session: Session) -> None:
        uid = _seed_user(usage_session)
        wid = _seed_workspace(usage_session, uid)
        summary = get_workspace_usage_summary(usage_session, workspace_id=wid)
        assert summary.totals == {}

    def test_user_summary_empty(self, usage_session: Session) -> None:
        uid = _seed_user(usage_session)
        summary = get_user_usage_summary(usage_session, owner_user_id=uid)
        assert summary.totals_by_event == {}
