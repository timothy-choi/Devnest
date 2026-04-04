"""UserAuth table — credentials for local sign-in (SQLModel / FastAPI stack)."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


class UserAuth(SQLModel, table=True):
    __tablename__ = "user_auth"

    user_auth_id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True, max_length=255)
    password_hash: str = Field(max_length=255)
    email: str = Field(index=True, unique=True, max_length=255)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
