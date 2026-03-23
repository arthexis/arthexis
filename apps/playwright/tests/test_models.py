from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.playwright import models as playwright_models
from apps.playwright.models import SessionCookie, WebsiteScreenshotSchedule, schedule_pending_website_screenshots


@pytest.mark.django_db
def test_schedule_pending_website_screenshots_executes_due_schedules(monkeypatch):
    now = timezone.now()
    schedule = WebsiteScreenshotSchedule.objects.create(
        slug="status",
        label="Status",
        url="https://example.com/status",
        sampling_period_minutes=5,
        last_sampled_at=now - timedelta(minutes=8),
    )

    executed_ids: list[int] = []

    def fake_execute(target, *, user=None):
        del user
        executed_ids.append(target.pk)
        target.last_sampled_at = now
        target.save(update_fields=["last_sampled_at"])

    monkeypatch.setattr(playwright_models, "execute_website_screenshot_schedule", fake_execute)

    ran = schedule_pending_website_screenshots(now=now)

    assert ran == [schedule.pk]
    assert executed_ids == [schedule.pk]


@pytest.mark.django_db
def test_schedule_pending_website_screenshots_continues_after_failure(monkeypatch):
    now = timezone.now()
    failing = WebsiteScreenshotSchedule.objects.create(
        slug="failing",
        label="Failing",
        url="https://example.com/failing",
        sampling_period_minutes=5,
        last_sampled_at=now - timedelta(minutes=8),
    )
    succeeding = WebsiteScreenshotSchedule.objects.create(
        slug="succeeding",
        label="Succeeding",
        url="https://example.com/succeeding",
        sampling_period_minutes=5,
        last_sampled_at=now - timedelta(minutes=8),
    )

    def fake_execute(target, *, user=None):
        del user
        if target.pk == failing.pk:
            raise RuntimeError("boom")

    monkeypatch.setattr(playwright_models, "execute_website_screenshot_schedule", fake_execute)

    ran = schedule_pending_website_screenshots(now=now)

    assert ran == [succeeding.pk]


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
def test_schedule_pending_website_screenshots_ignores_unscheduled_entries(monkeypatch):
    """Schedules without a cadence should not attempt execution."""

    WebsiteScreenshotSchedule.objects.create(
        slug="manual-only",
        label="Manual Only",
        url="https://example.com/manual",
        sampling_period_minutes=None,
    )

    monkeypatch.setattr(
        playwright_models,
        "execute_website_screenshot_schedule",
        lambda target, *, user=None: pytest.fail(f"unexpected execution for {target.pk}"),
    )

    assert schedule_pending_website_screenshots() == []
