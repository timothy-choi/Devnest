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
