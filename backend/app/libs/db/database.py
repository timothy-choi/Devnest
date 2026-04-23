"""SQLAlchemy engine and session factory shared by API, worker, and reconcile processes."""

import logging
from collections.abc import Generator

from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy import select

from ..common.config import format_database_url_for_log, get_settings
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
    WorkspaceCleanupTask,
    WorkspaceConfig,
    WorkspaceEvent,
    WorkspaceJob,
    WorkspaceRuntime,
    WorkspaceSecret,
    WorkspaceSession,
    WorkspaceSnapshot,
)
from ...services.audit_service.models import AuditLog  # noqa: F401 — register metadata
from ...services.usage_service.models import WorkspaceUsageRecord  # noqa: F401 — register metadata
from ...services.policy_service.models import Policy  # noqa: F401 — register metadata
from ...services.quota_service.models import Quota  # noqa: F401 — register metadata
from ...services.integration_service.models import (  # noqa: F401 — register metadata
    CITriggerRecord,
    UserProviderToken,
    WorkspaceCIConfig,
    WorkspaceRepository,
)
from ...services.node_execution_service.workspace_project_dir import prune_orphaned_workspace_project_dirs

_engine = None
_session_factory = None

_logger = logging.getLogger(__name__)


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
        settings = get_settings()
        _logger.info(
            "SQLAlchemy engine target: %s",
            format_database_url_for_log(settings.database_url),
        )
        _engine = create_engine(
            settings.database_url,
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
    from app.services.placement_service.bootstrap import ensure_default_local_execution_node

    settings = get_settings()
    if settings.devnest_db_auto_create:
        SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        ensure_default_local_execution_node(session)
        if settings.devnest_workspace_projects_prune_orphans_on_startup:
            live_refs = list(
                session.exec(
                    select(Workspace.workspace_id, Workspace.project_storage_key),  # type: ignore[arg-type]
                ).all()
            )
            prune_orphaned_workspace_project_dirs(settings.workspace_projects_base, live_refs)
        session.commit()
