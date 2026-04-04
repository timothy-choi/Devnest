from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.services.auth_service.services.register_service import (
    DuplicateEmailError,
    DuplicateUsernameError,
    register_user,
)

from ..dependencies import get_db
from ..schemas import RegisterRequest, RegisterResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
def register(body: RegisterRequest, session: Session = Depends(get_db)) -> RegisterResponse:
    try:
        user = register_user(
            session,
            username=body.username,
            email=str(body.email),
            password=body.password,
        )
    except DuplicateUsernameError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already registered") from None
    except DuplicateEmailError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered") from None
    assert user.user_auth_id is not None
    return RegisterResponse(
        user_auth_id=user.user_auth_id,
        username=user.username,
        email=user.email,
        created_at=user.created_at,
    )
