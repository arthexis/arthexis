"""Regression tests for automatic GitHub issue reporting feature gating."""

from __future__ import annotations

from pathlib import Path

from django.core.signals import got_request_exception
from django.test import RequestFactory

from apps.features.models import Feature
from apps.repos.apps import (
    _configure_github_issue_reporting,
    queue_github_issue,
)
from apps.repos.issue_reporting import GITHUB_ISSUE_REPORTING_FEATURE_SLUG


def _set_github_issue_reporting_feature(*, enabled: bool) -> Feature:
    """Create or update the suite feature state used by GitHub issue reporting tests."""

    feature, _created = Feature.objects.update_or_create(
        slug=GITHUB_ISSUE_REPORTING_FEATURE_SLUG,
        defaults={
            "display": "GitHub Issue Reporting",
            "is_enabled": enabled,
        },
    )
    return feature


def _build_request():
    """Return a request object that resembles a normal authenticated-free request."""

    request = RequestFactory().get("/boom")
    request.active_app = "repos"
    return request


def test_request_exceptions_do_not_enqueue_github_reporting_when_feature_disabled(
    db, monkeypatch, settings, tmp_path
):
    """Regression: disabled suite feature must block request exception reporting."""

    settings.BASE_DIR = tmp_path
    settings.GITHUB_ISSUE_REPORTING_ENABLED = True
    _set_github_issue_reporting_feature(enabled=False)

    enqueued: list[dict[str, object]] = []

    def fake_enqueue(task, payload):
        enqueued.append({"task": task, "payload": payload})

    monkeypatch.setattr("apps.repos.apps.enqueue_task", fake_enqueue)

    queue_github_issue(
        sender=None,
        request=_build_request(),
        exception=ValueError("disabled"),
    )

    assert enqueued == []
    assert not (Path(tmp_path) / ".locks" / "github-issues").exists()


def test_request_exceptions_enqueue_github_reporting_when_feature_enabled(
    db, monkeypatch, settings, tmp_path
):
    """Regression: enabled suite feature must enqueue request exception reporting."""

    settings.BASE_DIR = tmp_path
    settings.GITHUB_ISSUE_REPORTING_ENABLED = True
    _set_github_issue_reporting_feature(enabled=True)

    enqueued: list[dict[str, object]] = []

    def fake_enqueue(task, payload):
        enqueued.append({"task": task, "payload": payload})

    monkeypatch.setattr("apps.repos.apps.enqueue_task", fake_enqueue)

    queue_github_issue(
        sender=None,
        request=_build_request(),
        exception=ValueError("enabled"),
    )

    assert len(enqueued) == 1
    payload = enqueued[0]["payload"]
    assert payload["path"] == "/boom"
    assert payload["method"] == "GET"
    assert payload["active_app"] == "repos"
    assert payload["exception_class"] == "builtins.ValueError"
    assert payload["fingerprint"]
    assert (Path(tmp_path) / ".locks" / "github-issues" / payload["fingerprint"]).exists()


def test_duplicate_exception_cooldown_still_blocks_repeated_reporting(
    db, monkeypatch, settings, tmp_path
):
    """Regression: duplicate request exceptions should still respect cooldown lockfiles."""

    settings.BASE_DIR = tmp_path
    settings.GITHUB_ISSUE_REPORTING_ENABLED = True
    settings.GITHUB_ISSUE_REPORTING_COOLDOWN = 3600
    _set_github_issue_reporting_feature(enabled=True)

    enqueued: list[dict[str, object]] = []

    def fake_enqueue(task, payload):
        enqueued.append({"task": task, "payload": payload})

    monkeypatch.setattr("apps.repos.apps.enqueue_task", fake_enqueue)
    request = _build_request()

    queue_github_issue(sender=None, request=request, exception=ValueError("same"))
    queue_github_issue(sender=None, request=request, exception=ValueError("same"))

    assert len(enqueued) == 1


def test_signal_configuration_connects_runtime_gated_handler(db, monkeypatch, settings, tmp_path):
    """Signal registration should remain active so runtime feature toggles take effect."""

    settings.BASE_DIR = tmp_path
    settings.GITHUB_ISSUE_REPORTING_ENABLED = True
    _set_github_issue_reporting_feature(enabled=True)

    enqueued: list[dict[str, object]] = []

    def fake_enqueue(task, payload):
        enqueued.append({"task": task, "payload": payload})

    monkeypatch.setattr("apps.repos.apps.enqueue_task", fake_enqueue)
    got_request_exception.disconnect(dispatch_uid="apps.repos.github_issue_reporter")
    _configure_github_issue_reporting()

    try:
        got_request_exception.send(
            sender=object(),
            request=_build_request(),
            exception=RuntimeError("signal"),
        )
    finally:
        got_request_exception.disconnect(dispatch_uid="apps.repos.github_issue_reporter")
        _configure_github_issue_reporting()

    assert len(enqueued) == 1


def test_already_queued_reports_still_run_after_feature_is_disabled(
    db, settings, caplog
):
    """Queued reports should still execute even if the feature is later disabled."""

    settings.GITHUB_ISSUE_REPORTING_ENABLED = True
    _set_github_issue_reporting_feature(enabled=False)

    from apps.tasks.tasks import report_exception_to_github

    payload = {"fingerprint": "queued-before-disable"}

    with caplog.at_level("INFO"):
        report_exception_to_github(payload)

    assert "Queued GitHub issue report for queued-before-disable" in caplog.text
