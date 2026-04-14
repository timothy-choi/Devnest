"""Unit tests: default local execution node bootstrap."""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.services.placement_service.bootstrap import default_local_node_key, ensure_default_local_execution_node
from app.services.placement_service.models import ExecutionNode, ExecutionNodeProviderType, ExecutionNodeStatus


def test_ensure_default_idempotent() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    dev_settings = type(
        "S",
        (),
        {"devnest_env": "development", "database_url": ""},
    )()
    with patch("app.services.placement_service.bootstrap.get_settings", return_value=dev_settings):
        with Session(engine) as session:
            a = ensure_default_local_execution_node(session)
            session.commit()
            b = ensure_default_local_execution_node(session)
            session.commit()
            assert a.id == b.id
            assert a.node_key == default_local_node_key()
            assert a.provider_type == ExecutionNodeProviderType.LOCAL.value
            assert a.status == ExecutionNodeStatus.READY.value
            assert a.schedulable is True
            assert a.default_topology_id == 1
            rows = list(session.exec(select(ExecutionNode)).all())
            assert len(rows) == 1
