"""Change password for an authenticated user."""

import bcrypt
from sqlmodel import Session, select

from app.services.auth_service.models import Token, UserAuth


class InvalidCurrentPasswordError(Exception):
    """Current password does not match stored hash."""


def change_password(
    session: Session,
    *,
    user: UserAuth,
    current_password: str,
    new_password: str,
) -> None:
    if not bcrypt.checkpw(current_password.encode("utf-8"), user.password_hash.encode("utf-8")):
        raise InvalidCurrentPasswordError

    user.password_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    session.add(user)

    assert user.user_auth_id is not None
    rows = session.exec(select(Token).where(Token.user_id == user.user_auth_id)).all()
    for row in rows:
        if not row.revoked:
            row.revoked = True
            session.add(row)

    session.commit()
    session.refresh(user)
