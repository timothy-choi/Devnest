"""SQLAlchemy engine and session factory. Connection string from DATABASE_URL only."""

from collections.abc import Generator

from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine

from ..common.config import get_settings
from ..topology.models import (  # noqa: F401 — register metadata
    IpAllocation,
    Topology,
    TopologyAttachment,
    TopologyRuntime,
)
from ...services.auth_service.models import OAuth, PasswordResetToken, Token, UserAuth  # noqa: F401 — register metadata
from ...services.notification_service.models import (  # noqa: F401 — register metadata
    Notification,
    NotificationDelivery,
    NotificationPreference,
    NotificationRecipient,
    PushSubscription,
)
from ...services.user_service.models import UserProfile, UserSettings  # noqa: F401 — register metadata
from ...services.placement_service.models import ExecutionNode  # noqa: F401 — register metadata
from ...services.workspace_service.models import (  # noqa: F401 — register metadata
    Workspace,
    WorkspaceConfig,
    WorkspaceEvent,
    WorkspaceJob,
    WorkspaceRuntime,
    WorkspaceSession,
    WorkspaceSnapshot,
)
from ...services.audit_service.models import AuditLog  # noqa: F401 — register metadata
from ...services.usage_service.models import WorkspaceUsageRecord  # noqa: F401 — register metadata

_engine = None
_session_factory = None


def reset_engine() -> None:
    """Dispose engine and clear factories (for tests when DATABASE_URL changes)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url,
            echo=False,
            pool_pre_ping=True,
        )
    return _engine


def _session_factory_fn():
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            class_=Session,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
    return _session_factory


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one session per request, always closed."""
    factory = _session_factory_fn()
    db = factory()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    from app.services.placement_service.bootstrap import ensure_default_local_execution_node

    with Session(engine) as session:
        ensure_default_local_execution_node(session)
        session.commit()
