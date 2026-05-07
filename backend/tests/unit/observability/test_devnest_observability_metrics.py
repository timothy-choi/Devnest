"""Unit tests for DevNest Prometheus metrics (gauges refresh + workspace counters)."""

from __future__ import annotations

from prometheus_client import generate_latest

from app.libs.observability import metrics as m
from app.services.placement_service.models import ExecutionNode
from app.services.workspace_service.api.schemas.workspace_schemas import CreateWorkspaceRequest
from app.services.workspace_service.models import Workspace
from app.services.workspace_service.models.enums import WorkspaceStatus
from app.services.workspace_service.services.workspace_intent_service import create_workspace
from sqlmodel import Session, select


def _sum_metric(body: bytes, metric_name: str) -> float:
    """Sum all sample values for lines matching ``metric_name{...}``."""
    total = 0.0
    prefix = metric_name + "{"
    plain = metric_name + " "
    for line in body.decode().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        token = line.split()[0]
        if token == metric_name:
            total += float(line.split()[-1])
        elif token.startswith(prefix):
            total += float(line.split()[-1])
        elif token.startswith(plain):
            total += float(line.split()[-1])
    return total


def test_metrics_response_is_prometheus_text() -> None:
    body, media_type = m.metrics_response_body()
    assert media_type.startswith("text/plain")
    assert b"# HELP " in body
    assert b"# TYPE " in body
    assert b"devnest_queue_depth" in body


def test_exposition_includes_production_metric_names() -> None:
    body = generate_latest()
    for name in (
        b"devnest_workspace_created_total",
        b"devnest_workspace_failed_total",
        b"devnest_workspace_retried_total",
        b"devnest_autoscaler_scale_up_total",
        b"devnest_autoscaler_scale_down_total",
        b"devnest_node_cleanup_total",
        b"devnest_chaos_recovery_total",
        b"devnest_active_workspaces",
        b"devnest_ready_nodes",
        b"devnest_provisioning_nodes",
        b"devnest_draining_nodes",
        b"devnest_pending_workspace_jobs",
        b"devnest_workspace_provision_seconds",
        b"devnest_node_bootstrap_seconds",
        b"devnest_scale_down_seconds",
        b"devnest_ssm_command_seconds",
    ):
        assert name in body


def test_refresh_gauges_active_workspaces_and_nodes(workspace_unit_engine, owner_user_id: int) -> None:
    with Session(workspace_unit_engine) as session:
        loc = session.exec(select(ExecutionNode)).first()
        assert loc is not None
        loc.disk_free_mb = 12345
        loc.memory_free_mb = 4096
        session.add(loc)
        ws = Workspace(
            name="metric-ws",
            description="",
            owner_user_id=owner_user_id,
            project_storage_key="abc123metric",
            status=WorkspaceStatus.RUNNING.value,
            execution_node_id=loc.id,
            is_private=True,
        )
        session.add(ws)
        session.commit()

        m.refresh_gauges_from_db(session)

    body = generate_latest()
    assert _sum_metric(body, "devnest_active_workspaces") == 1.0
    assert _sum_metric(body, "devnest_ready_nodes") >= 1.0
    assert b"devnest_node_disk_free_mb" in body
    assert b"devnest_node_memory_free_mb" in body


def test_workspace_created_total_increments_on_create_workspace(
    workspace_unit_engine,
    owner_user_id: int,
) -> None:
    before = _sum_metric(generate_latest(), "devnest_workspace_created_total")
    body = CreateWorkspaceRequest.model_validate(
        {
            "name": "obs-created",
            "runtime": {
                "image": "alpine:3.19",
                "cpu_limit_cores": 1,
                "memory_limit_mib": 512,
            },
        }
    )
    with Session(workspace_unit_engine) as session:
        create_workspace(session, owner_user_id=owner_user_id, body=body)
    after = _sum_metric(generate_latest(), "devnest_workspace_created_total")
    assert after == before + 1.0


def test_workspace_failed_total_increments(workspace_unit_engine, owner_user_id: int) -> None:
    before = _sum_metric(generate_latest(), "devnest_workspace_failed_total")
    with Session(workspace_unit_engine) as session:
        loc = session.exec(select(ExecutionNode)).first()
        assert loc is not None
        ws = Workspace(
            name="metric-fail",
            description="",
            owner_user_id=owner_user_id,
            project_storage_key="failmetric123",
            status=WorkspaceStatus.ERROR.value,
            execution_node_id=loc.id,
            is_private=True,
        )
        session.add(ws)
        session.commit()
        session.refresh(ws)
        m.record_workspace_failed(
            workspace_status=ws.status,
            failure_reason="unit_test",
            node_key="unknown",
            provider_type="local",
        )
    after = _sum_metric(generate_latest(), "devnest_workspace_failed_total")
    assert after == before + 1.0
