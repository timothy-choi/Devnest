"""Encrypted workspace secret storage for runtime-only injection."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from app.services.integration_service.token_crypto import decrypt_token, encrypt_token
from app.services.workspace_service.models import Workspace, WorkspaceSecret

AI_PROVIDER_SECRET_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def _get_owned_workspace(session: Session, *, workspace_id: int, owner_user_id: int) -> Workspace:
    row = session.exec(
        select(Workspace).where(
            Workspace.workspace_id == workspace_id,
            Workspace.owner_user_id == owner_user_id,
        )
    ).first()
    if row is None:
        raise ValueError("workspace_not_found_or_not_owned")
    return row


def upsert_workspace_ai_secret(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    provider: str,
    api_key: str,
) -> WorkspaceSecret:
    provider_name = (provider or "").strip().lower()
    secret_name = AI_PROVIDER_SECRET_ENV.get(provider_name)
    if not secret_name:
        raise ValueError("unsupported_ai_provider")
    key_value = (api_key or "").strip()
    if not key_value:
        raise ValueError("api_key_required")

    _get_owned_workspace(session, workspace_id=workspace_id, owner_user_id=owner_user_id)
    now = datetime.now(timezone.utc)
    row = session.exec(
        select(WorkspaceSecret).where(
            WorkspaceSecret.workspace_id == workspace_id,
            WorkspaceSecret.secret_name == secret_name,
        )
    ).first()
    encrypted_value = encrypt_token(key_value)
    if row is None:
        row = WorkspaceSecret(
            workspace_id=workspace_id,
            secret_name=secret_name,
            encrypted_value=encrypted_value,
            created_at=now,
            updated_at=now,
        )
    else:
        row.encrypted_value = encrypted_value
        row.updated_at = now
    session.add(row)
    session.flush()
    return row


def delete_workspace_ai_secret(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    provider: str,
) -> bool:
    provider_name = (provider or "").strip().lower()
    secret_name = AI_PROVIDER_SECRET_ENV.get(provider_name)
    if not secret_name:
        raise ValueError("unsupported_ai_provider")

    _get_owned_workspace(session, workspace_id=workspace_id, owner_user_id=owner_user_id)
    row = session.exec(
        select(WorkspaceSecret).where(
            WorkspaceSecret.workspace_id == workspace_id,
            WorkspaceSecret.secret_name == secret_name,
        )
    ).first()
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def resolve_workspace_runtime_secret_env(session: Session, *, workspace_id: int) -> dict[str, str]:
    rows = session.exec(
        select(WorkspaceSecret).where(WorkspaceSecret.workspace_id == workspace_id)
    ).all()
    env: dict[str, str] = {}
    for row in rows:
        env[row.secret_name] = decrypt_token(row.encrypted_value)
    return env
