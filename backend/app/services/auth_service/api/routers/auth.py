from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.services.auth_service.models import UserAuth
from app.services.auth_service.services.login_service import InvalidCredentialsError, login_user
from app.services.auth_service.services.logout_service import UnknownRefreshTokenError, logout_refresh_token
from app.services.auth_service.services.register_service import (
    DuplicateEmailError,
    DuplicateUsernameError,
    register_user,
)

from ..deps_auth import get_current_user
from ..dependencies import get_db
from ..schemas import (
    AuthProfileResponse,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    LogoutResponse,
    RegisterRequest,
    RegisterResponse,
)

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


@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Log in and receive access + refresh tokens",
)
def login(body: LoginRequest, session: Session = Depends(get_db)) -> LoginResponse:
    try:
        tokens = login_user(session, username=body.username, password=body.password)
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        ) from None
    return LoginResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
    )


@router.post(
    "/logout",
    response_model=LogoutResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke a refresh token (logout this session)",
)
def logout(body: LogoutRequest, session: Session = Depends(get_db)) -> LogoutResponse:
    try:
        logout_refresh_token(session, refresh_token=body.refresh_token)
    except UnknownRefreshTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unknown or already revoked refresh token",
        ) from None
    return LogoutResponse()


@router.get(
    "",
    response_model=AuthProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Current user auth record",
)
def get_auth_profile(current: UserAuth = Depends(get_current_user)) -> AuthProfileResponse:
    assert current.user_auth_id is not None
    return AuthProfileResponse(
        user_auth_id=current.user_auth_id,
        username=current.username,
        email=current.email,
        created_at=current.created_at,
    )
