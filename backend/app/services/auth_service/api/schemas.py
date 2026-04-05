"""Pydantic request/response models for auth HTTP API.

When this module grows, consider splitting into ``schemas/auth_requests.py`` and
``schemas/auth_responses.py`` (re-export from ``schemas/__init__.py``).
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class RegisterResponse(BaseModel):
    user_auth_id: int
    username: str
    email: str
    created_at: datetime


class AuthProfileResponse(BaseModel):
    """Public auth record for the authenticated user (GET /auth)."""

    user_auth_id: int
    username: str
    email: str
    created_at: datetime


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=2048)


class LogoutResponse(BaseModel):
    message: str = "Logged out"


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class ChangePasswordResponse(BaseModel):
    message: str = "Password updated"


class RefreshAccessResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class OAuthStartResponse(BaseModel):
    """Authorization URL to redirect the user to (GitHub or Google)."""

    authorization_url: str


class OAuthCallbackResponse(BaseModel):
    """Access token in body; refresh token is also set as HttpOnly cookie refresh_token."""

    access_token: str
    token_type: str = "bearer"


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    """
    Generic success message. If PASSWORD_RESET_RETURN_TOKEN=true, reset_token is set for local testing
    (replace with email delivery in production).
    """

    message: str = "If an account exists for this email, you will receive password reset instructions."
    reset_token: str | None = None


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1, max_length=2048)
    new_password: str = Field(min_length=8, max_length=256)


class ResetPasswordResponse(BaseModel):
    message: str = "Password has been reset"
