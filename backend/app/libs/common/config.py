import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14
    password_reset_token_expire_minutes: int = 60
    # If true, PUT /auth/forgot-password includes reset_token in JSON (local/testing only; use email in production).
    password_reset_return_token: bool = False

    # OAuth redirect bases (no trailing slash). Env may be host:port only; code prepends http:// if needed.
    github_oauth_public_base_url: str = ""
    gcloud_oauth_public_base_url: str = ""
    oauth_github_client_id: str = ""
    oauth_github_client_secret: str = ""
    oauth_google_client_id: str = ""
    oauth_google_client_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
