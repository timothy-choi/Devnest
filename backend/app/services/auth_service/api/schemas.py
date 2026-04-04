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
