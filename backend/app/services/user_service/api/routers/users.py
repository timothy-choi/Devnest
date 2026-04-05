"""User profile routes (authenticated ``/me`` + public lookup by id)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.libs.db.database import get_db
from app.services.auth_service.api.dependencies import get_current_user
from app.services.auth_service.models import UserAuth
from app.services.user_service.api.schemas import MyProfileResponse, PublicUserProfileResponse, UpdateMyProfileRequest
from app.services.user_service.services import user_profile_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "/me",
    response_model=MyProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Current user profile",
)
def get_my_profile(
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> MyProfileResponse:
    assert current.user_auth_id is not None
    profile = user_profile_service.get_my_profile(session, current.user_auth_id)
    return MyProfileResponse.model_validate(profile)


@router.patch(
    "/me",
    response_model=MyProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Update current user profile",
)
def patch_my_profile(
    body: UpdateMyProfileRequest,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> MyProfileResponse:
    assert current.user_auth_id is not None
    profile = user_profile_service.update_my_profile(session, current.user_auth_id, body)
    return MyProfileResponse.model_validate(profile)


@router.get(
    "/{user_id}",
    response_model=PublicUserProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Public profile by user id",
)
def get_public_profile(user_id: int, session: Session = Depends(get_db)) -> PublicUserProfileResponse:
    out = user_profile_service.get_public_profile(session, user_id)
    if out is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found") from None
    return out
