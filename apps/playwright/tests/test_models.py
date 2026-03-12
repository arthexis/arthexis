from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.groups.models import SecurityGroup
from apps.playwright import models as playwright_models
from apps.playwright.models import (
    InvalidCookiePayloadError,
    PlaywrightBrowser,
    PlaywrightEngineFeatureDisabledError,
    PlaywrightRuntimeDisabledError,
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
    monkeypatch.setattr(playwright_models, "is_feature_active_for_node", lambda *, node, slug: True)
    monkeypatch.setattr(playwright_models, "is_suite_feature_enabled", lambda slug, default=True: True)
    monkeypatch.setattr("apps.nodes.models.Node.get_local", lambda: object())

    driver = browser.create_driver()
    assert launch_kwargs["headless"] is True
    assert launch_kwargs["executable_path"] == "/opt/custom/chromium"
    driver.quit()


@pytest.mark.django_db
def test_create_driver_requires_suite_feature(monkeypatch):
    browser = PlaywrightBrowser.objects.create(
        name="disabled-runtime",
        engine=PlaywrightBrowser.Engine.CHROMIUM,
        mode=PlaywrightBrowser.Mode.HEADLESS,
    )

    monkeypatch.setattr(playwright_models, "is_suite_feature_enabled", lambda slug, default=True: False)

    with pytest.raises(PlaywrightRuntimeDisabledError):
        browser.create_driver()


@pytest.mark.django_db
def test_create_driver_requires_engine_node_feature(monkeypatch):
    browser = PlaywrightBrowser.objects.create(
        name="engine-check",
        engine=PlaywrightBrowser.Engine.FIREFOX,
        mode=PlaywrightBrowser.Mode.HEADLESS,
    )

    class DummyNode:
        pass

    monkeypatch.setattr(playwright_models, "is_suite_feature_enabled", lambda slug, default=True: True)
    monkeypatch.setattr("apps.nodes.models.Node.get_local", lambda: DummyNode())
    monkeypatch.setattr(playwright_models, "is_feature_active_for_node", lambda *, node, slug: False)

    with pytest.raises(PlaywrightEngineFeatureDisabledError):
        browser.create_driver()


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

    cookie.set_cookies(
        [{"name": "sessionid", "value": "abc123", "domain": ".example.com", "path": "/"}]
    )

    assert cookie.pk is not None
    cookie.refresh_from_db()
    assert cookie.cookies == [
        {"name": "sessionid", "value": "abc123", "domain": ".example.com", "path": "/"}
    ]


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


@pytest.mark.django_db
def test_schedule_pending_website_screenshots_short_circuits_when_suite_disabled(monkeypatch):
    WebsiteScreenshotSchedule.objects.create(
        slug="disabled",
        label="Disabled",
        url="https://example.com/disabled",
        sampling_period_minutes=5,
    )

    monkeypatch.setattr(playwright_models, "is_suite_feature_enabled", lambda slug, default=True: False)

    ran = schedule_pending_website_screenshots()

    assert ran == []
