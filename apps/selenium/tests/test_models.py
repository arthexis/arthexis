from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.playwright.models import InvalidCookiePayloadError, SessionCookie


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


@pytest.mark.django_db
def test_session_cookie_set_cookies_default_save_on_unsaved_instance():
    user = get_user_model().objects.create_user(username="bootstrap-owner", password="secret")
    cookie = SessionCookie(name="Bootstrap Cookie", user=user)

    cookie.set_cookies([{"name": "sessionid", "value": "abc123", "domain": ".example.com", "path": "/"}])

    assert cookie.pk is not None
    cookie.refresh_from_db()
    assert cookie.cookies == [{"name": "sessionid", "value": "abc123", "domain": ".example.com", "path": "/"}]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload",
    [
        [{"name": None, "value": "abc"}],
        [{"name": "", "value": "abc"}],
        [{"name": "sessionid", "value": 123}],
    ],
)
def test_session_cookie_set_cookies_rejects_invalid_name_or_value_types(payload):
    user = get_user_model().objects.create_user(username="type-owner", password="secret")
    cookie = SessionCookie(name="Type Validation", user=user)

    with pytest.raises(InvalidCookiePayloadError):
        cookie.set_cookies(payload, save=False)


@pytest.mark.django_db
def test_session_cookie_mark_helpers_default_save_on_unsaved_instance():
    user = get_user_model().objects.create_user(username="helper-owner", password="secret")

    used_cookie = SessionCookie(name="Used Cookie", user=user)
    used_cookie.mark_used()
    assert used_cookie.pk is not None

    valid_cookie = SessionCookie(name="Valid Cookie", user=user)
    valid_cookie.mark_valid()
    assert valid_cookie.pk is not None

    rejected_cookie = SessionCookie(name="Rejected Cookie", user=user)
    rejected_cookie.mark_rejected("bad credentials")
    assert rejected_cookie.pk is not None
    rejected_cookie.refresh_from_db()
    assert rejected_cookie.rejection_count == 1
    assert rejected_cookie.last_rejection_reason == "bad credentials"


@pytest.mark.django_db
def test_session_cookie_mark_rejected_atomic_increment():
    user = get_user_model().objects.create_user(username="atomic-owner", password="secret")
    cookie = SessionCookie.objects.create(name="Atomic", user=user, rejection_count=2)

    cookie.mark_rejected("expired")

    cookie.refresh_from_db()
    assert cookie.rejection_count == 3
    assert cookie.state == SessionCookie.State.REJECTED
    assert cookie.last_rejection_reason == "expired"
