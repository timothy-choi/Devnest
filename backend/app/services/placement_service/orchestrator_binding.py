"""Map workspace jobs to orchestrator placement (node_key + topology_id).

``node_key`` selects the :class:`~app.services.placement_service.models.ExecutionNode` row used by
:mod:`app.services.node_execution_service` to build runtime + host command execution (local Docker,
``ssh_docker``, or ``ssm_docker``). The same key is persisted on ``WorkspaceRuntime.node_id`` after
bring-up. New placement for bring-up class jobs goes through :mod:`app.services.scheduler_service`
(policy + explain); row locking remains in :mod:`app.services.placement_service.node_placement`.

"""

from __future__ import annotations

import os

from sqlmodel import Session, select

from app.services.scheduler_service.service import schedule_workspace
from app.services.workspace_service.models import Workspace, WorkspaceJob, WorkspaceJobType, WorkspaceRuntime

from .errors import InvalidPlacementParametersError, NoSchedulableNodeError


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

    **Caveat:** ``RECONCILE_RUNTIME`` with no ``WorkspaceRuntime`` row (or empty ``node_id``)
    uses env fallback — acceptable for V1 local dev; multi-node should ensure runtime rows
    exist or extend this path to call placement (TODO).

    **START** reuses ``WorkspaceRuntime.node_id`` / ``topology_id`` when present so bring-up
    targets the same execution node as the last successful run (authoritative placement). Fresh
    workspaces (no persisted node) still schedule via ``schedule_workspace``.
    """
    wid = ws.workspace_id
    assert wid is not None
    jt = job.job_type
    rt = _runtime_row(session, wid)

    if jt == WorkspaceJobType.START.value and rt is not None:
        nk = (rt.node_id or "").strip()
        if nk and rt.topology_id is not None:
            return nk, int(rt.topology_id)

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
            WorkspaceJobType.SNAPSHOT_CREATE.value,
            WorkspaceJobType.SNAPSHOT_RESTORE.value,
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
        # TODO: drive requested_cpu / requested_memory_mb from versioned WorkspaceConfig when exposed.
        sch = schedule_workspace(session, workspace_id=wid)
        if sch.invalid_request:
            raise InvalidPlacementParametersError(sch.message)
        if sch.execution_node is None:
            raise NoSchedulableNodeError(sch.message)
        node = sch.execution_node
        topo = node.default_topology_id if node.default_topology_id is not None else _topology_id_from_env()
        return node.node_key, int(topo)

    return _node_key_from_env(), _topology_id_from_env()
