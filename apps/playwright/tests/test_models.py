from datetime import timedelta

import pytest
from django.utils import timezone

from apps.groups.models import SecurityGroup
from apps.playwright import models as playwright_models
from apps.playwright.models import (
    PlaywrightBrowser,
    PlaywrightScript,
    SessionCookie,
    WebsiteScreenshotSchedule,
    schedule_pending_website_screenshots,
)


@pytest.mark.django_db
def test_create_driver_passes_binary_path(monkeypatch):
    browser = PlaywrightBrowser.objects.create(
        name="custom",
        engine=PlaywrightBrowser.Engine.CHROMIUM,
        mode=PlaywrightBrowser.Mode.HEADLESS,
        binary_path=" /opt/custom/chromium ",
    )

    launch_kwargs = {}

    class DummyLauncher:
        def launch(self, **kwargs):
            launch_kwargs.update(kwargs)

            class DummyBrowser:
                def new_context(self):
                    class DummyContext:
                        def new_page(self):
                            class DummyPage:
                                pass

                            return DummyPage()

                    return DummyContext()

            return DummyBrowser()

    class DummyPlaywright:
        chromium = DummyLauncher()
        firefox = DummyLauncher()
        webkit = DummyLauncher()

        def stop(self):
            return None

    class DummyFactory:
        def start(self):
            return DummyPlaywright()

    monkeypatch.setattr(playwright_models, "_load_sync_playwright", lambda: (lambda: DummyFactory()))

    driver = browser.create_driver()
    assert launch_kwargs["headless"] is True
    assert launch_kwargs["executable_path"] == "/opt/custom/chromium"
    driver.quit()


def test_playwright_script_supports_legacy_url_preamble():
    script = PlaywrightScript(
        name="legacy",
        start_url="",
        script="\nhttps://example.com/path\n\nbrowser.click('button')\n",
    )

    start_url, body = script._resolved_start_url_and_body()

    assert start_url == "https://example.com/path"
    assert body == "browser.click('button')"


@pytest.mark.django_db
def test_sessioncookie_set_cookies_saves_unsaved_instance():
    group = SecurityGroup.objects.create(name="Playwright Operators")
    cookie = SessionCookie(name="new-cookie", group=group)

    cookie.set_cookies([{"name": "session", "value": "abc"}])

    assert cookie.pk is not None
    cookie.refresh_from_db()
    assert cookie.cookies == [{"name": "session", "value": "abc"}]


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
