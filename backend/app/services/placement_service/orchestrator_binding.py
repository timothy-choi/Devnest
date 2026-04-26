"""Map workspace jobs to orchestrator placement (node_key + topology_id).

``node_key`` selects the :class:`~app.services.placement_service.models.ExecutionNode` row used by
:mod:`app.services.node_execution_service` to build runtime + host command execution (local Docker,
``ssh_docker``, or ``ssm_docker``). The same key is persisted on ``WorkspaceRuntime.node_id`` after
bring-up. New placement for bring-up class jobs goes through :mod:`app.services.scheduler_service`
(policy + explain); row locking remains in :mod:`app.services.placement_service.node_placement`.

**Production:** placement is authoritative — :class:`~app.services.workspace_service.models.WorkspaceRuntime`
must carry ``node_id`` and ``topology_id`` for every job that mutates or inspects runtime state,
except ``CREATE`` (which schedules fresh, or uses **operator pinned** placement when
:mod:`app.services.placement_service.operator_pinned_create` matches) and ``START`` on a workspace with no runtime row yet (first start).
``REPO_IMPORT`` also requires that persisted runtime (it runs git in the placed container via the node
execution bundle; the worker resolves placement but does not build a full orchestrator for that job).
New scheduling in staging/production requires ``ExecutionNode.default_topology_id``; env-based
``DEVNEST_TOPOLOGY_ID`` is **not** used for new placement when strict mode is on. Legacy
``DEVNEST_NODE_ID`` / ``DEVNEST_TOPOLOGY_ID`` fallback for *existing* incomplete rows is **disabled**
unless ``DEVNEST_ENV=development`` and ``DEVNEST_ALLOW_RUNTIME_ENV_FALLBACK=true``.

"""

from __future__ import annotations

import os

from sqlmodel import Session, select

from app.libs.topology.models import Topology
from app.services.placement_service.models import ExecutionNode, ExecutionNodeStatus
from app.services.scheduler_service.service import schedule_workspace
from app.services.workspace_service.models import Workspace, WorkspaceJob, WorkspaceJobType, WorkspaceRuntime

from .errors import AuthoritativePlacementError, InvalidPlacementParametersError, NoSchedulableNodeError
from .operator_pinned_create import workspace_uses_operator_pinned_create
from .runtime_policy import placement_strict_enforced, runtime_env_fallback_allowed, runtime_placement_row_complete


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


def _validate_topology_catalog_exists(session: Session, topology_id: int) -> None:
    """Fail placement early if the topology catalog has no row for this id (misconfigured DB)."""
    row = session.get(Topology, int(topology_id))
    if row is None:
        raise InvalidPlacementParametersError(
            f"topology id {int(topology_id)} has no Topology catalog row. "
            "Run database migrations and bootstrap (init_db seeds a default topology in development), "
            "or insert a topology row for this id. "
            "Check ExecutionNode.default_topology_id and DEVNEST_TOPOLOGY_ID match an existing topology.",
        )


def _schedule_workspace_placement(session: Session, workspace_id: int) -> tuple[str, int]:
    sch = schedule_workspace(session, workspace_id=workspace_id)
    if sch.invalid_request:
        raise InvalidPlacementParametersError(sch.message)
    if sch.execution_node is None:
        raise NoSchedulableNodeError(sch.message)
    node = sch.execution_node
    if placement_strict_enforced():
        if node.default_topology_id is None:
            raise InvalidPlacementParametersError(
                "Strict production/staging placement requires execution_node.default_topology_id for new "
                "workload placement; set it on the selected ExecutionNode (env-based DEVNEST_TOPOLOGY_ID "
                "is not permitted for scheduling in this mode).",
            )
        topo = int(node.default_topology_id)
    else:
        topo = node.default_topology_id if node.default_topology_id is not None else _topology_id_from_env()
    tid = int(topo)
    _validate_topology_catalog_exists(session, tid)
    return node.node_key, tid


def _pinned_operator_create_placement(session: Session, ws: Workspace) -> tuple[str, int]:
    """Placement for operator pinned test workspaces (no scheduler)."""
    assert ws.execution_node_id is not None
    node = session.get(ExecutionNode, int(ws.execution_node_id))
    if node is None:
        raise InvalidPlacementParametersError(
            f"pinned CREATE: execution_node id={int(ws.execution_node_id)} not found",
        )
    if (node.status or "").strip() != ExecutionNodeStatus.READY.value:
        raise InvalidPlacementParametersError(
            f"pinned CREATE: execution_node {node.node_key!r} is not READY (status={node.status!r})",
        )
    if not bool(node.schedulable):
        raise InvalidPlacementParametersError(
            f"pinned CREATE: execution_node {node.node_key!r} is not schedulable",
        )
    if placement_strict_enforced():
        if node.default_topology_id is None:
            raise InvalidPlacementParametersError(
                "Strict production/staging placement requires execution_node.default_topology_id for "
                "pinned operator CREATE; set it on the target ExecutionNode.",
            )
        topo = int(node.default_topology_id)
    else:
        topo = node.default_topology_id if node.default_topology_id is not None else _topology_id_from_env()
    tid = int(topo)
    _validate_topology_catalog_exists(session, tid)
    nk = (node.node_key or "").strip()
    if not nk:
        raise InvalidPlacementParametersError("pinned CREATE: execution_node has empty node_key")
    return nk, tid


_JOBS_NEED_RUNTIME_PLACEMENT = frozenset(
    {
        WorkspaceJobType.START.value,
        WorkspaceJobType.STOP.value,
        WorkspaceJobType.DELETE.value,
        WorkspaceJobType.RECONCILE_RUNTIME.value,
        WorkspaceJobType.RESTART.value,
        WorkspaceJobType.UPDATE.value,
        WorkspaceJobType.SNAPSHOT_CREATE.value,
        WorkspaceJobType.SNAPSHOT_RESTORE.value,
        WorkspaceJobType.REPO_IMPORT.value,
    },
)


def resolve_orchestrator_placement(
    session: Session,
    ws: Workspace,
    job: WorkspaceJob,
) -> tuple[str, int]:
    """
    Return ``(node_key, topology_id)`` for building :class:`DefaultOrchestratorService`.

    - **CREATE** always calls the scheduler (new placement).
    - **START** reuses complete ``WorkspaceRuntime`` when present; otherwise schedules when there is
      no runtime row or (development only) an incomplete row may be replaced by a fresh schedule.
    - **STOP / DELETE / RECONCILE / RESTART / UPDATE / snapshots / REPO_IMPORT** require complete runtime
      placement in production; development may use env fallback when explicitly enabled.
    """
    wid = ws.workspace_id
    assert wid is not None
    jt = job.job_type
    rt = _runtime_row(session, wid)
    strict = placement_strict_enforced()

    if jt == WorkspaceJobType.CREATE.value:
        if workspace_uses_operator_pinned_create(ws):
            return _pinned_operator_create_placement(session, ws)
        return _schedule_workspace_placement(session, wid)

    if jt not in _JOBS_NEED_RUNTIME_PLACEMENT:
        if runtime_env_fallback_allowed():
            nk, tid = _node_key_from_env(), _topology_id_from_env()
            _validate_topology_catalog_exists(session, int(tid))
            return nk, int(tid)
        raise AuthoritativePlacementError(
            f"Job type {jt!r} has no authoritative placement rule; set DEVNEST_ALLOW_RUNTIME_ENV_FALLBACK=true "
            "in development or extend resolve_orchestrator_placement.",
        )

    if runtime_placement_row_complete(rt):
        assert rt is not None
        return str(rt.node_id).strip(), int(rt.topology_id)  # type: ignore[arg-type]

    # START: first bring-up may have no runtime row yet; reschedule when placement is incomplete.
    if jt == WorkspaceJobType.START.value:
        if rt is not None and strict:
            raise AuthoritativePlacementError(
                "START requires complete WorkspaceRuntime (node_id and topology_id) in production/staging, "
                "or run cleanup/reconcile to repair a partial row.",
            )
        return _schedule_workspace_placement(session, wid)

    if strict:
        raise AuthoritativePlacementError(
            f"Job {jt} requires WorkspaceRuntime with node_id and topology_id; none found for workspace_id={wid}.",
        )

    if runtime_env_fallback_allowed():
        nk, tid = _node_key_from_env(), _topology_id_from_env()
        _validate_topology_catalog_exists(session, int(tid))
        return nk, int(tid)

    raise AuthoritativePlacementError(
        "WorkspaceRuntime placement is incomplete and env fallback is disabled "
        "(set DEVNEST_ALLOW_RUNTIME_ENV_FALLBACK=true for local development only).",
    )
