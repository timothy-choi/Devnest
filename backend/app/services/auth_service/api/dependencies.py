"""Route dependencies: database session and authenticated user (Bearer JWT)."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.libs.db.database import get_db
from app.services.auth_service.models import UserAuth
from app.services.auth_service.services.auth_profile_service import UserAuthNotFoundError, get_user_auth_entry
from app.services.auth_service.services.auth_token import decode_access_user_id

security = HTTPBearer(auto_error=False)


def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    session: Annotated[Session, Depends(get_db)],
) -> UserAuth:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    try:
        user_id = decode_access_user_id(creds.credentials)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
        ) from None
    try:
        return get_user_auth_entry(session, user_id=user_id)
    except UserAuthNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        ) from None
