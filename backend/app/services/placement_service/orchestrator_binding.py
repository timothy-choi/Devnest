"""Map workspace jobs to orchestrator placement (node_key + topology_id).

V1: Docker on the API/worker host; ``node_key`` selects the logical node row and is persisted on
``WorkspaceRuntime.node_id`` after bring-up. Multi-node later: each key maps to an agent/EC2
instance; runtime/topology adapters gain remote backends (TODO).

"""

from __future__ import annotations

import os

from sqlmodel import Session, select

from app.services.workspace_service.models import Workspace, WorkspaceJob, WorkspaceJobType, WorkspaceRuntime

from .node_placement import reserve_node_for_workspace


def _topology_id_from_env() -> int:
    raw = (os.environ.get("DEVNEST_TOPOLOGY_ID") or "1").strip()
    try:
        return int(raw, 10)
    except ValueError:
        return 1


def _node_key_from_env() -> str:
    return (os.environ.get("DEVNEST_NODE_ID") or "node-1").strip() or "node-1"


def _runtime_row(session: Session, workspace_id: int) -> WorkspaceRuntime | None:
    return session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id)).first()


def resolve_orchestrator_placement(
    session: Session,
    ws: Workspace,
    job: WorkspaceJob,
) -> tuple[str, int]:
    """
    Return ``(node_key, topology_id)`` for building :class:`DefaultOrchestratorService`.

    - Jobs that target an existing attachment prefer :class:`WorkspaceRuntime` placement.
    - Bring-up class jobs select via placement policy when no usable runtime placement exists.
    - Otherwise fall back to process env (legacy single-node dev).
    """
    wid = ws.workspace_id
    assert wid is not None
    jt = job.job_type
    rt = _runtime_row(session, wid)

    reuse_from_runtime = (
        rt is not None
        and rt.node_id
        and str(rt.node_id).strip()
        and jt
        in (
            WorkspaceJobType.STOP.value,
            WorkspaceJobType.DELETE.value,
            WorkspaceJobType.RECONCILE_RUNTIME.value,
            WorkspaceJobType.RESTART.value,
            WorkspaceJobType.UPDATE.value,
        )
    )
    if reuse_from_runtime:
        node_key = str(rt.node_id).strip()
        topo = rt.topology_id if rt.topology_id is not None else _topology_id_from_env()
        return node_key, int(topo)

    needs_new_placement = jt in (
        WorkspaceJobType.CREATE.value,
        WorkspaceJobType.START.value,
        WorkspaceJobType.RESTART.value,
        WorkspaceJobType.UPDATE.value,
    )
    if needs_new_placement:
        node = reserve_node_for_workspace(session, workspace_id=wid)
        topo = node.default_topology_id if node.default_topology_id is not None else _topology_id_from_env()
        return node.node_key, int(topo)

    return _node_key_from_env(), _topology_id_from_env()
