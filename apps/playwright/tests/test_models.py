import pytest
from django.contrib.auth import get_user_model

from apps.playwright import models as playwright_models
from apps.playwright.models import SessionCookie, schedule_pending_website_screenshots


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


def test_schedule_pending_website_screenshots_is_noop(monkeypatch):
    monkeypatch.setattr(
        playwright_models,
        "execute_website_screenshot_schedule",
        lambda target, *, user=None: pytest.fail(f"unexpected execution for {target.pk}"),
    )

    assert schedule_pending_website_screenshots() == []
