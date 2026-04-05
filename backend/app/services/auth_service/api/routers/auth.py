from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlmodel import Session

from app.libs.common.config import get_settings
from app.services.auth_service.models import UserAuth
from app.services.auth_service.services.login_service import InvalidCredentialsError, login_user
from app.services.auth_service.services.logout_service import UnknownRefreshTokenError, logout_refresh_token
from app.services.auth_service.services.password_reset_service import (
    InvalidResetTokenError,
    request_password_reset,
    reset_password_with_token,
)
from app.services.auth_service.services.password_service import InvalidCurrentPasswordError, change_password
from app.services.auth_service.services.refresh_token_service import InvalidRefreshTokenError, refresh_access_token
from app.services.auth_service.services.register_service import (
    DuplicateEmailError,
    DuplicateUsernameError,
    register_user,
)
from app.services.auth_service.services.oauth_client import OAuthProviderError
from app.services.auth_service.services.oauth_service import (
    UnsupportedOAuthProviderError,
    complete_oauth,
    start_oauth_authorization_url,
)
from app.services.auth_service.services.oauth_state import OAuthStateError

from ..dependencies import get_current_user, get_db
from ..schemas import (
    AuthProfileResponse,
    ChangePasswordRequest,
    ChangePasswordResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    LogoutResponse,
    OAuthCallbackResponse,
    OAuthStartResponse,
    RefreshAccessResponse,
    RegisterRequest,
    RegisterResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/oauth/{oauth_provider}",
    response_model=OAuthStartResponse,
    status_code=status.HTTP_200_OK,
    summary="Start OAuth: returns provider authorization URL (GitHub or Google)",
)
def oauth_start(oauth_provider: str) -> OAuthStartResponse:
    try:
        url = start_oauth_authorization_url(provider=oauth_provider)
    except UnsupportedOAuthProviderError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported OAuth provider",
        ) from None
    except OAuthProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        ) from e
    return OAuthStartResponse(authorization_url=url)


@router.get(
    "/oauth/{oauth_provider}/callback",
    response_model=OAuthCallbackResponse,
    status_code=status.HTTP_200_OK,
    summary="OAuth callback: exchange code, create/link user, return access JWT + refresh cookie",
)
def oauth_callback(
    oauth_provider: str,
    session: Session = Depends(get_db),
    code: str = Query(..., min_length=1),
    state: str = Query(..., min_length=1),
) -> JSONResponse:
    try:
        tokens = complete_oauth(session, provider=oauth_provider, code=code, state=state)
    except UnsupportedOAuthProviderError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported OAuth provider",
        ) from None
    except OAuthStateError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state",
        ) from None
    except OAuthProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        ) from e

    s = get_settings()
    max_age = s.refresh_token_expire_days * 86400
    body = OAuthCallbackResponse(access_token=tokens.access_token).model_dump()
    resp = JSONResponse(status_code=status.HTTP_200_OK, content=body)
    resp.set_cookie(
        key="refresh_token",
        value=tokens.refresh_token,
        httponly=True,
        max_age=max_age,
        samesite="lax",
        path="/",
    )
    return resp


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
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    status_code=status.HTTP_200_OK,
    summary="Request password reset (same response whether or not the email is registered)",
)
def forgot_password(body: ForgotPasswordRequest, session: Session = Depends(get_db)) -> ForgotPasswordResponse:
    raw = request_password_reset(session, email=str(body.email))
    s = get_settings()
    if s.password_reset_return_token and raw is not None:
        return ForgotPasswordResponse(reset_token=raw)
    return ForgotPasswordResponse()


@router.put(
    "/reset-password",
    response_model=ResetPasswordResponse,
    status_code=status.HTTP_200_OK,
    summary="Set a new password using a reset token from the forgot-password flow",
)
def reset_password_endpoint(body: ResetPasswordRequest, session: Session = Depends(get_db)) -> ResetPasswordResponse:
    try:
        reset_password_with_token(session, token=body.token, new_password=body.new_password)
    except InvalidResetTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        ) from None
    return ResetPasswordResponse()


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
