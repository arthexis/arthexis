import pytest

from apps.tasks import tasks


@pytest.mark.integration
def test_run_scheduled_website_screenshots_short_circuits_when_suite_disabled(monkeypatch):
    monkeypatch.setattr("apps.playwright.models.is_suite_feature_enabled", lambda slug, default=True: False)

    result = tasks.run_scheduled_website_screenshots()

    assert result == []


def test_run_scheduled_website_screenshots_executes_when_suite_enabled(monkeypatch):
    monkeypatch.setattr("apps.playwright.models.schedule_pending_website_screenshots", lambda: [1, 2])

    result = tasks.run_scheduled_website_screenshots()

    assert result == [1, 2]
