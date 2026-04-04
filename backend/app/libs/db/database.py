"""PostgreSQL engine and session factory (imports auth_service models for metadata)."""

from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from ..common.config import get_settings
from ...services.auth_service.models import OAuth, Token, UserAuth  # noqa: F401 — register metadata

_settings = get_settings()
engine = create_engine(
    _settings.database_url,
    echo=False,
    pool_pre_ping=True,
)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
