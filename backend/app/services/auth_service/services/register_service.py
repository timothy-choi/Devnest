"""User registration domain logic (no HTTP)."""

import bcrypt
from sqlmodel import Session, select

from app.services.auth_service.models import UserAuth


class DuplicateUsernameError(Exception):
    """Username already exists."""


class DuplicateEmailError(Exception):
    """Email already exists."""


def register_user(
    session: Session,
    *,
    username: str,
    email: str,
    password: str,
) -> UserAuth:
    if session.exec(select(UserAuth).where(UserAuth.username == username)).first():
        raise DuplicateUsernameError
    if session.exec(select(UserAuth).where(UserAuth.email == email)).first():
        raise DuplicateEmailError

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    user = UserAuth(username=username, email=email, password_hash=password_hash)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user
