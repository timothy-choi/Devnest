from collections.abc import Generator

from sqlmodel import Session

from app.libs.db.database import get_session


def get_db() -> Generator[Session, None, None]:
    yield from get_session()
