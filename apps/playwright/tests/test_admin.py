"""Admin tests for Playwright models."""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.urls import NoReverseMatch, reverse

from apps.playwright.models import PlaywrightBrowser, WebsiteScreenshotSchedule


@pytest.mark.django_db
def test_playwright_admin_does_not_register_script_model():
    """The removed PlaywrightScript admin surface should stay unavailable."""

    model_names = {model.__name__ for model in admin.site._registry}

    assert "PlaywrightScript" not in model_names
    with pytest.raises(NoReverseMatch):
        reverse("admin:playwright_playwrightscript_changelist")


@pytest.mark.django_db
def test_screenshot_schedule_admin_still_exposes_run_now_action(admin_client, monkeypatch):
    """Screenshot scheduling should remain available through Django admin."""

    schedule = WebsiteScreenshotSchedule.objects.create(
        slug="status-page",
        label="Status Page",
        url="https://example.com/status",
    )
    executed: list[int] = []

    def fake_execute(target, *, user=None):
        assert user is not None
        executed.append(target.pk)

    monkeypatch.setattr(
        "apps.playwright.admin.execute_website_screenshot_schedule",
        fake_execute,
    )

    response = admin_client.post(
        reverse("admin:playwright_websitescreenshotschedule_changelist"),
        {
            "action": "run_now",
            "_selected_action": [str(schedule.pk)],
        },
        follow=True,
    )

    assert response.status_code == 200
    assert executed == [schedule.pk]
    content = response.content.decode()
    assert 'value="run_now"' in content
    assert "Run screenshot schedule now" in content


@pytest.mark.django_db
def test_playwright_browser_admin_still_registers_after_script_removal():
    """Browser admin registration should remain intact after script cleanup."""

    assert PlaywrightBrowser in admin.site._registry
