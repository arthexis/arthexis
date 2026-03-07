from datetime import timedelta

import pytest
from django.utils import timezone

from apps.playwright import models as playwright_models
from apps.playwright.models import PlaywrightBrowser, WebsiteScreenshotSchedule, schedule_pending_website_screenshots


@pytest.mark.django_db
def test_browser_engine_candidates_filters_by_node_features(monkeypatch):
    schedule = WebsiteScreenshotSchedule.objects.create(
        slug="landing",
        label="Landing",
        url="https://example.com",
        favored_engine=PlaywrightBrowser.Engine.CHROMIUM,
        fallback_engines=[PlaywrightBrowser.Engine.FIREFOX, PlaywrightBrowser.Engine.WEBKIT],
    )

    class DummyNode:
        enabled = {"playwright-browser-firefox"}

        def has_feature(self, slug: str) -> bool:
            return slug in self.enabled

    monkeypatch.setattr("apps.nodes.models.Node.get_local", lambda: DummyNode())

    assert schedule.browser_engine_candidates() == [PlaywrightBrowser.Engine.FIREFOX]


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
