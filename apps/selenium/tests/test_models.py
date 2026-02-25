from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.selenium.models import InvalidCookiePayloadError, SessionCookie


@pytest.mark.django_db
def test_session_cookie_mark_rejected_and_valid_cycle():
    user = get_user_model().objects.create_user(username="portal-owner", password="secret")
    cookie = SessionCookie.objects.create(name="Portal Session", user=user)

    cookie.mark_rejected("session expired")

    cookie.refresh_from_db()
    assert cookie.state == SessionCookie.State.REJECTED
    assert cookie.rejection_count == 1
    assert cookie.last_rejection_reason == "session expired"

    cookie.mark_valid()

    cookie.refresh_from_db()
    assert cookie.state == SessionCookie.State.ACTIVE
    assert cookie.last_rejection_reason == ""
    assert cookie.last_validated_at is not None


@pytest.mark.django_db
def test_session_cookie_set_cookies_requires_name_and_value():
    user = get_user_model().objects.create_user(username="broken-owner", password="secret")
    cookie = SessionCookie(name="Broken", user=user)

    with pytest.raises(InvalidCookiePayloadError):
        cookie.set_cookies([{"name": "session"}], save=False)


@pytest.mark.django_db
def test_session_cookie_expiry_check():
    user = get_user_model().objects.create_user(username="expiry-owner", password="secret")
    cookie = SessionCookie(
        name="Expiry",
        user=user,
        expires_at=timezone.now() - timedelta(minutes=1),
    )

    assert cookie.is_expired() is True


@pytest.mark.django_db
def test_session_cookie_clean_requires_single_owner():
    user_model = get_user_model()
    user = user_model.objects.create_user(username="owner-user", password="secret")

    cookie = SessionCookie(name="Owned Cookie")
    with pytest.raises(ValidationError):
        cookie.full_clean()

    cookie.user = user
    cookie.full_clean()
