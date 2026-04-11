"""Unit tests for account deletion and related row purge."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import bcrypt
import pytest
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session, create_engine, select

from app.services.auth_service.models import OAuth, PasswordResetToken, Token, UserAuth
from app.services.auth_service.services.delete_account_service import (
    InvalidAccountPasswordError,
    _purge_user_related_rows,
    delete_account_for_current_user,
)
from app.services.notification_service.models import (
    Notification,
    NotificationDelivery,
    NotificationPreference,
    NotificationRecipient,
    PushSubscription,
)
from app.services.user_service.models import UserProfile, UserSettings


def _sqlite_engine() -> Engine:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


def test_delete_account_raises_when_user_auth_id_missing() -> None:
    user = UserAuth(username="x", email="x@x.com", password_hash="h")
    session = MagicMock()
    with pytest.raises(InvalidAccountPasswordError):
        delete_account_for_current_user(session, user, password="anything")


@patch("app.services.auth_service.services.delete_account_service._purge_user_related_rows")
@patch("app.services.auth_service.services.delete_account_service._user_has_oauth_link")
def test_delete_account_local_user_requires_password(mock_has_oauth: MagicMock, mock_purge: MagicMock) -> None:
    mock_has_oauth.return_value = False
    ph = bcrypt.hashpw(b"correct", bcrypt.gensalt()).decode()
    user = UserAuth(user_auth_id=1, username="a", email="a@a.com", password_hash=ph)
    session = MagicMock()
    session.get.return_value = user

    with pytest.raises(InvalidAccountPasswordError):
        delete_account_for_current_user(session, user, password=None)

    mock_purge.assert_not_called()
    session.delete.assert_not_called()
    session.commit.assert_not_called()


@patch("app.services.auth_service.services.delete_account_service._purge_user_related_rows")
@patch("app.services.auth_service.services.delete_account_service._user_has_oauth_link")
def test_delete_account_local_user_rejects_wrong_password(
    mock_has_oauth: MagicMock,
    mock_purge: MagicMock,
) -> None:
    mock_has_oauth.return_value = False
    ph = bcrypt.hashpw(b"correct", bcrypt.gensalt()).decode()
    user = UserAuth(user_auth_id=2, username="b", email="b@b.com", password_hash=ph)
    session = MagicMock()

    with pytest.raises(InvalidAccountPasswordError):
        delete_account_for_current_user(session, user, password="wrong-password")

    mock_purge.assert_not_called()


@patch("app.services.auth_service.services.delete_account_service._purge_user_related_rows")
@patch("app.services.auth_service.services.delete_account_service._user_has_oauth_link")
def test_delete_account_oauth_user_allows_no_password(
    mock_has_oauth: MagicMock,
    mock_purge: MagicMock,
) -> None:
    mock_has_oauth.return_value = True
    user = UserAuth(user_auth_id=5, username="c", email="c@c.com", password_hash="irrelevant")
    session = MagicMock()
    session.get.return_value = user

    delete_account_for_current_user(session, user, password=None)

    mock_purge.assert_called_once_with(session, 5)
    session.delete.assert_called_once_with(user)
    session.commit.assert_called_once()


@patch("app.services.auth_service.services.delete_account_service._user_has_oauth_link")
def test_delete_account_local_success_purges_deletes_user(mock_has_oauth: MagicMock) -> None:
    mock_has_oauth.return_value = False
    engine = _sqlite_engine()
    ph = bcrypt.hashpw(b"secret99", bcrypt.gensalt()).decode()
    with Session(engine) as session:
        user = UserAuth(username="delme", email="delme@example.com", password_hash=ph)
        session.add(user)
        session.commit()
        session.refresh(user)
        uid = user.user_auth_id
        assert uid is not None

        session.add(
            NotificationPreference(
                user_id=uid,
                notification_type="ALERTS",
                in_app_enabled=True,
                email_enabled=True,
                push_enabled=True,
            )
        )
        session.commit()

        delete_account_for_current_user(session, user, password="secret99")

        assert session.get(UserAuth, uid) is None
        prefs = session.exec(
            select(NotificationPreference).where(NotificationPreference.user_id == uid),
        ).all()
        assert prefs == []


def test_purge_user_related_rows_removes_notification_chain_and_related_tables() -> None:
    engine = _sqlite_engine()
    exp = datetime.now(timezone.utc) + timedelta(days=1)
    with Session(engine) as session:
        user = UserAuth(username="u1", email="u1@example.com", password_hash="h")
        session.add(user)
        session.commit()
        session.refresh(user)
        uid = user.user_auth_id
        assert uid is not None

        notif = Notification(type="t", title="title", body="body", source_service="test")
        session.add(notif)
        session.commit()
        session.refresh(notif)

        recipient = NotificationRecipient(
            notification_id=notif.notification_id,
            user_id=uid,
            status="PENDING",
        )
        session.add(recipient)
        session.commit()
        session.refresh(recipient)
        rid = recipient.notification_recipient_id
        assert rid is not None

        delivery = NotificationDelivery(
            notification_id=notif.notification_id,
            notification_recipient_id=rid,
            channel="email",
        )
        session.add(delivery)

        session.add(
            NotificationPreference(
                user_id=uid,
                notification_type="TASK",
                in_app_enabled=True,
                email_enabled=False,
                push_enabled=True,
            )
        )
        session.add(
            PushSubscription(
                user_id=uid,
                platform="web",
                endpoint="https://push.example/x",
            )
        )
        session.add(
            Token(
                user_id=uid,
                token_hash="hash1",
                expires_at=exp,
                revoked=False,
            )
        )
        session.add(
            OAuth(
                user_id=uid,
                oauth_provider="github",
                provider_user_id="gh-1",
            )
        )
        session.add(
            PasswordResetToken(
                user_id=uid,
                token_hash="rhash",
                expires_at=exp,
                used=False,
            )
        )
        session.add(
            UserProfile(
                user_id=uid,
                display_name="N",
            )
        )
        session.add(UserSettings(user_id=uid))
        session.commit()

        _purge_user_related_rows(session, uid)
        session.commit()

        assert session.exec(select(NotificationDelivery)).first() is None
        assert session.exec(select(NotificationRecipient)).first() is None
        assert session.exec(select(NotificationPreference).where(NotificationPreference.user_id == uid)).first() is None
        assert session.exec(select(PushSubscription).where(PushSubscription.user_id == uid)).first() is None
        assert session.exec(select(Token).where(Token.user_id == uid)).first() is None
        assert session.exec(select(OAuth).where(OAuth.user_id == uid)).first() is None
        assert session.exec(
            select(PasswordResetToken).where(PasswordResetToken.user_id == uid)
        ).first() is None
        assert session.get(UserProfile, uid) is None
        assert session.get(UserSettings, uid) is None

        # Core notification row is not user-scoped; purge must not delete it.
        assert session.get(Notification, notif.notification_id) is not None
        assert session.get(UserAuth, uid) is not None
