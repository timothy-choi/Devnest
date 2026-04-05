"""Profile reads and updates for the authenticated user and public lookup."""

from __future__ import annotations

from sqlmodel import Session

from app.services.user_service.api.schemas import PublicUserProfileResponse, UpdateMyProfileRequest
from app.services.user_service.models import UserProfile
from app.services.user_service.repositories import user_profile_repo


def get_my_profile(session: Session, current_user_id: int) -> UserProfile:
    """Return the current user's profile, creating a minimal row if none exists yet."""
    return user_profile_repo.upsert_profile_if_missing(session, current_user_id)


def update_my_profile(
    session: Session,
    current_user_id: int,
    update_data: UpdateMyProfileRequest,
) -> UserProfile:
    """
    Apply PATCH semantics: only fields present in the request body are updated.
    """
    profile = user_profile_repo.upsert_profile_if_missing(session, current_user_id)
    patch = update_data.model_dump(exclude_unset=True)
    for key, value in patch.items():
        setattr(profile, key, value)
    return user_profile_repo.update_profile(session, profile)


def get_public_profile(session: Session, user_id: int) -> PublicUserProfileResponse | None:
    """
    Public-safe projection for ``GET /users/{id}``.

    Returns ``None`` when no profile row exists (route layer should map to 404).
    """
    row = user_profile_repo.get_public_by_user_id(session, user_id)
    if row is None:
        return None
    return PublicUserProfileResponse.model_validate(row)
