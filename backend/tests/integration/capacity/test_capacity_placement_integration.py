"""PostgreSQL integration: placement + reservation ledger."""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from app.services.auth_service.models.user_auth import UserAuth
from app.services.placement_service.errors import NoSchedulableNodeError
from app.services.placement_service.models import ExecutionNode
from app.services.placement_service.node_placement import select_node_for_workspace
from app.services.workspace_service.models import Workspace, WorkspaceRuntime
from app.services.workspace_service.models.enums import WorkspaceStatus


def test_placement_raises_when_effective_capacity_blocked_by_runtime_reservation(
    db_session: Session,
) -> None:
    node = db_session.exec(select(ExecutionNode)).first()
    assert node is not None
    key = node.node_key
    node.allocatable_cpu = 2.0
    node.allocatable_memory_mb = 2048
    node.allocatable_disk_mb = 10_240
    db_session.add(node)
    db_session.commit()

    u = UserAuth(username="cap_int_u", password_hash="x", email="cap_int_u@example.com")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)

    ws = Workspace(name="cap_ws", owner_user_id=u.user_auth_id, status=WorkspaceStatus.RUNNING.value)
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)

    db_session.add(
        WorkspaceRuntime(
            workspace_id=ws.workspace_id,
            node_id=key,
            reserved_cpu=1.5,
            reserved_memory_mb=1536,
            reserved_disk_mb=8192,
        ),
    )
    db_session.commit()

    with pytest.raises(NoSchedulableNodeError, match="effective_free"):
        select_node_for_workspace(
            db_session,
            workspace_id=42,
            requested_cpu=1.0,
            requested_memory_mb=1024,
            requested_disk_mb=3072,
        )

    picked = select_node_for_workspace(
        db_session,
        workspace_id=43,
        requested_cpu=0.25,
        requested_memory_mb=256,
        requested_disk_mb=1024,
    )
    assert picked.node_key == key


def test_stop_clears_reservation_so_capacity_frees(db_session: Session) -> None:
    from app.workers.workspace_job_worker.worker import _apply_runtime_stop
    from app.services.orchestrator_service.results import WorkspaceStopResult

    node = db_session.exec(select(ExecutionNode)).first()
    assert node is not None
    key = node.node_key

    u = UserAuth(username="cap_int_u2", password_hash="x", email="cap_int_u2@example.com")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)

    ws = Workspace(name="cap_ws2", owner_user_id=u.user_auth_id, status=WorkspaceStatus.RUNNING.value)
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)

    rt = WorkspaceRuntime(
        workspace_id=ws.workspace_id,
        node_id=key,
        reserved_cpu=1.0,
        reserved_memory_mb=512,
        reserved_disk_mb=4096,
    )
    db_session.add(rt)
    db_session.commit()

    ws.status = WorkspaceStatus.STOPPED.value
    db_session.add(ws)
    db_session.commit()

    _apply_runtime_stop(
        db_session,
        ws.workspace_id,
        WorkspaceStopResult(workspace_id=str(ws.workspace_id), success=True),
    )
    db_session.commit()

    db_session.refresh(rt)
    assert rt.reserved_cpu == 0.0
    assert rt.reserved_memory_mb == 0
    assert rt.reserved_disk_mb == 0
