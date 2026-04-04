"""Load UserAuth by id (no HTTP)."""

from sqlmodel import Session

from app.services.auth_service.models import UserAuth


class UserAuthNotFoundError(Exception):
    """No UserAuth row for the given id."""


def get_user_auth_entry(session: Session, *, user_id: int) -> UserAuth:
    user = session.get(UserAuth, user_id)
    if user is None:
        raise UserAuthNotFoundError
    return user
