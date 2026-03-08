from django.contrib import admin, messages
from django.test import RequestFactory

import pytest

from apps.nodes.feature_checks import ScreenshotRuntimeCapability
from apps.playwright.admin import PlaywrightBrowserAdmin
from apps.playwright.models import PlaywrightBrowser


@pytest.mark.django_db
def test_test_browsers_failure_reports_centralized_diagnostics(monkeypatch):
    browser = PlaywrightBrowser.objects.create(name="browser-a", engine=PlaywrightBrowser.Engine.CHROMIUM)

    def _raise_startup_error():
        raise RuntimeError("startup failed")

    monkeypatch.setattr(browser, "create_driver", _raise_startup_error)
    monkeypatch.setattr(
        "apps.playwright.admin.get_screenshot_runtime_capability",
        lambda: ScreenshotRuntimeCapability(
            ready=False,
            display_available=False,
            diagnostics=("DISPLAY: not set (headless recommended)",),
            error_message="Screenshot Poll prerequisites failed: chromium missing",
            level=messages.ERROR,
        ),
    )

    captured: list[tuple[str, int]] = []
    model_admin = PlaywrightBrowserAdmin(PlaywrightBrowser, admin.site)
    monkeypatch.setattr(model_admin, "message_user", lambda _request, message, level=messages.INFO: captured.append((str(message), level)))

    request = RequestFactory().post("/admin/playwright/playwrightbrowser/")
    model_admin.test_browsers(request, PlaywrightBrowser.objects.filter(pk=browser.pk))

    assert captured
    assert "Diagnostics:" in captured[0][0]
    assert "Screenshot Poll prerequisites failed: chromium missing" in captured[0][0]
    assert captured[0][1] == messages.ERROR
