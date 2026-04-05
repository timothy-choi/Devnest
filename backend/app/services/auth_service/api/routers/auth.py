from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, status
from sqlmodel import Session

from app.services.auth_service.models import UserAuth
from app.services.auth_service.services.login_service import InvalidCredentialsError, login_user
from app.services.auth_service.services.logout_service import UnknownRefreshTokenError, logout_refresh_token
from app.services.auth_service.services.password_service import InvalidCurrentPasswordError, change_password
from app.services.auth_service.services.refresh_token_service import InvalidRefreshTokenError, refresh_access_token
from app.services.auth_service.services.register_service import (
    DuplicateEmailError,
    DuplicateUsernameError,
    register_user,
)

from ..deps_auth import get_current_user
from ..dependencies import get_db
from ..schemas import (
    AuthProfileResponse,
    ChangePasswordRequest,
    ChangePasswordResponse,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    LogoutResponse,
    RefreshAccessResponse,
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


@router.put(
    "/password",
    response_model=ChangePasswordResponse,
    status_code=status.HTTP_200_OK,
    summary="Change password for the authenticated user",
)
def change_password_endpoint(
    body: ChangePasswordRequest,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> ChangePasswordResponse:
    try:
        change_password(
            session,
            user=current,
            current_password=body.current_password,
            new_password=body.new_password,
        )
    except InvalidCurrentPasswordError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        ) from None
    return ChangePasswordResponse()


@router.get(
    "/refresh_token",
    response_model=RefreshAccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Issue a new access JWT using a refresh token",
)
def refresh_token_endpoint(
    session: Session = Depends(get_db),
    refresh_token: str | None = Query(
        default=None,
        description="Refresh token (prefer header or cookie in production)",
    ),
    x_refresh_token: str | None = Header(default=None, alias="X-Refresh-Token"),
    refresh_cookie: str | None = Cookie(default=None, alias="refresh_token"),
) -> RefreshAccessResponse:
    raw = refresh_token or x_refresh_token or refresh_cookie
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing refresh token (query refresh_token, header X-Refresh-Token, or cookie refresh_token)",
        )
    try:
        access = refresh_access_token(session, refresh_token_plain=raw)
    except InvalidRefreshTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        ) from None
    return RefreshAccessResponse(access_token=access)


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
