from datetime import timedelta

import pytest
from django.utils import timezone

from apps.selenium.models import InvalidCookiePayloadError, SessionCookie


@pytest.mark.django_db
def test_session_cookie_mark_rejected_and_valid_cycle():
    cookie = SessionCookie.objects.create(name="Portal Session")

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


def test_session_cookie_set_cookies_requires_name_and_value():
    cookie = SessionCookie(name="Broken")

    with pytest.raises(InvalidCookiePayloadError):
        cookie.set_cookies([{"name": "session"}], save=False)


def test_session_cookie_expiry_check():
    cookie = SessionCookie(
        name="Expiry",
        expires_at=timezone.now() - timedelta(minutes=1),
    )

    assert cookie.is_expired() is True
