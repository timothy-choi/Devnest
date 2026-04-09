"""Unit tests for user_profile_service (mocked repositories, no HTTP)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services.user_service.api.schemas import UpdateMyProfileRequest
from app.services.user_service.models import UserProfile
from app.services.user_service.services import user_profile_service


def _profile(
    *,
    user_id: int = 1,
    display_name: str = "Ada",
    first_name: str | None = "Ada",
    last_name: str | None = "Lovelace",
    bio: str | None = "Bio text",
    avatar_url: str | None = "https://example.com/a.png",
    tz: str | None = "America/New_York",
    locale: str | None = "en-US",
) -> UserProfile:
    ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    return UserProfile(
        user_id=user_id,
        display_name=display_name,
        first_name=first_name,
        last_name=last_name,
        bio=bio,
        avatar_url=avatar_url,
        timezone=tz,
        locale=locale,
        created_at=ts,
        updated_at=ts,
    )


@patch("app.services.user_service.services.user_profile_service.user_profile_repo.upsert_profile_if_missing")
def test_get_my_profile_returns_upserted_row(mock_upsert: MagicMock) -> None:
    session = MagicMock()
    expected = _profile(user_id=7)
    mock_upsert.return_value = expected

    out = user_profile_service.get_my_profile(session, 7)

    assert out is expected
    mock_upsert.assert_called_once_with(session, 7)


@patch("app.services.user_service.services.user_profile_service.user_profile_repo.update_profile")
@patch("app.services.user_service.services.user_profile_service.user_profile_repo.upsert_profile_if_missing")
def test_update_my_profile_updates_only_fields_sent_in_request(
    mock_upsert: MagicMock,
    mock_update: MagicMock,
) -> None:
    session = MagicMock()
    profile = _profile(user_id=3, display_name="Old", first_name="F", last_name="L")
    mock_upsert.return_value = profile
    mock_update.side_effect = lambda s, p: p

    body = UpdateMyProfileRequest(display_name="New Name")
    out = user_profile_service.update_my_profile(session, 3, body)

    assert out.display_name == "New Name"
    assert out.first_name == "F"
    assert out.last_name == "L"
    mock_update.assert_called_once_with(session, profile)


@patch("app.services.user_service.services.user_profile_service.user_profile_repo.update_profile")
@patch("app.services.user_service.services.user_profile_service.user_profile_repo.upsert_profile_if_missing")
def test_update_my_profile_partial_multiple_fields(mock_upsert: MagicMock, mock_update: MagicMock) -> None:
    session = MagicMock()
    profile = _profile(user_id=2, bio="old bio", tz="UTC", locale="en")
    mock_upsert.return_value = profile
    mock_update.side_effect = lambda s, p: p

    body = UpdateMyProfileRequest(bio="new bio", locale="fr-FR")
    user_profile_service.update_my_profile(session, 2, body)

    assert profile.bio == "new bio"
    assert profile.locale == "fr-FR"
    assert profile.timezone == "UTC"
    assert profile.display_name == "Ada"


@patch("app.services.user_service.services.user_profile_service.user_profile_repo.update_profile")
@patch("app.services.user_service.services.user_profile_service.user_profile_repo.upsert_profile_if_missing")
def test_update_my_profile_with_empty_patch_still_calls_update(
    mock_upsert: MagicMock,
    mock_update: MagicMock,
) -> None:
    """Request with no fields set (PATCH {}) yields exclude_unset={}; row unchanged but persisted."""
    session = MagicMock()
    profile = _profile()
    mock_upsert.return_value = profile
    mock_update.side_effect = lambda s, p: p

    body = UpdateMyProfileRequest()
    assert body.model_dump(exclude_unset=True) == {}

    user_profile_service.update_my_profile(session, 1, body)

    mock_update.assert_called_once_with(session, profile)
    assert profile.display_name == "Ada"


@patch("app.services.user_service.services.user_profile_service.user_profile_repo.get_public_by_user_id")
def test_get_public_profile_returns_none_when_repo_has_no_row(mock_get: MagicMock) -> None:
    mock_get.return_value = None
    session = MagicMock()

    assert user_profile_service.get_public_profile(session, 99) is None
    mock_get.assert_called_once_with(session, 99)


@patch("app.services.user_service.services.user_profile_service.user_profile_repo.get_public_by_user_id")
def test_get_public_profile_maps_row_and_excludes_private_fields(mock_get: MagicMock) -> None:
    row = _profile(
        user_id=10,
        display_name="Public",
        first_name="P",
        last_name="U",
        bio="About",
        avatar_url="https://x/1.png",
        tz="Europe/London",
        locale="en-GB",
    )
    mock_get.return_value = row
    session = MagicMock()

    out = user_profile_service.get_public_profile(session, 10)
    assert out is not None

    data = out.model_dump()
    assert data == {
        "user_id": 10,
        "display_name": "Public",
        "first_name": "P",
        "last_name": "U",
        "bio": "About",
        "avatar_url": "https://x/1.png",
    }
    assert "timezone" not in data
    assert "locale" not in data
    assert "created_at" not in data
    assert "updated_at" not in data
