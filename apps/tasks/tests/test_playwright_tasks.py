import pytest

from apps.tasks import tasks


def test_run_scheduled_website_screenshots_is_noop(monkeypatch):
    monkeypatch.setattr(
        "apps.playwright.models.schedule_pending_website_screenshots",
        lambda: pytest.fail("automatic Playwright schedule execution should stay disabled"),
    )

    result = tasks.run_scheduled_website_screenshots()

    assert result == []
