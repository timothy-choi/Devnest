"""Unit tests: node capacity accounting (reservation sums)."""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.services.auth_service.models.user_auth import UserAuth
from app.services.placement_service.capacity import total_reserved_disk_mb_on_node_key, total_reserved_on_node_key
from app.services.workspace_service.models import Workspace, WorkspaceRuntime
from app.services.workspace_service.models.enums import WorkspaceStatus


@pytest.fixture
def cap_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _add_user_workspace(session: Session, *, status: str) -> tuple[int, int]:
    u = UserAuth(username="cu", password_hash="x", email="cu@e.com")
    session.add(u)
    session.commit()
    session.refresh(u)
    assert u.user_auth_id is not None
    ws = Workspace(name="cw", owner_user_id=u.user_auth_id, status=status)
    session.add(ws)
    session.commit()
    session.refresh(ws)
    assert ws.workspace_id is not None
    return u.user_auth_id, ws.workspace_id


def test_total_reserved_sums_running_workloads(cap_engine) -> None:
    with Session(cap_engine) as session:
        _, wid = _add_user_workspace(session, status=WorkspaceStatus.RUNNING.value)
        session.add(
            WorkspaceRuntime(
                workspace_id=wid,
                node_id="n1",
                reserved_cpu=1.0,
                reserved_memory_mb=512,
                reserved_disk_mb=4096,
            )
        )
        session.commit()
        c, m = total_reserved_on_node_key(session, "n1")
        d = total_reserved_disk_mb_on_node_key(session, "n1")
        assert c == pytest.approx(1.0)
        assert m == 512
        assert d == 4096


def test_stopped_workspace_excluded_from_sum(cap_engine) -> None:
    with Session(cap_engine) as session:
        _, wid = _add_user_workspace(session, status=WorkspaceStatus.STOPPED.value)
        session.add(
            WorkspaceRuntime(
                workspace_id=wid,
                node_id="n1",
                reserved_cpu=2.0,
                reserved_memory_mb=1024,
            )
        )
        session.commit()
        c, m = total_reserved_on_node_key(session, "n1")
        assert c == pytest.approx(0.0)
        assert m == 0


def test_deleted_workspace_excluded_from_sum(cap_engine) -> None:
    with Session(cap_engine) as session:
        _, wid = _add_user_workspace(session, status=WorkspaceStatus.DELETED.value)
        session.add(
            WorkspaceRuntime(
                workspace_id=wid,
                node_id="n1",
                reserved_cpu=2.0,
                reserved_memory_mb=1024,
            )
        )
        session.commit()
        c, m = total_reserved_on_node_key(session, "n1")
        assert c == pytest.approx(0.0)
        assert m == 0


def test_error_workspace_excluded_from_sum(cap_engine) -> None:
    with Session(cap_engine) as session:
        _, wid = _add_user_workspace(session, status=WorkspaceStatus.ERROR.value)
        session.add(
            WorkspaceRuntime(
                workspace_id=wid,
                node_id="n1",
                reserved_cpu=3.0,
                reserved_memory_mb=2048,
            )
        )
        session.commit()
        c, m = total_reserved_on_node_key(session, "n1")
        assert c == pytest.approx(0.0)
        assert m == 0


def test_unpinned_runtime_row_not_counted_toward_node(cap_engine) -> None:
    """Ledger rows without a concrete ``node_id`` must not attribute reservation to any node_key."""
    with Session(cap_engine) as session:
        _, wid = _add_user_workspace(session, status=WorkspaceStatus.RUNNING.value)
        session.add(
            WorkspaceRuntime(
                workspace_id=wid,
                node_id=None,
                reserved_cpu=99.0,
                reserved_memory_mb=9999,
            )
        )
        session.commit()
        c, m = total_reserved_on_node_key(session, "n1")
        assert c == pytest.approx(0.0)
        assert m == 0
