from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.signals import got_request_exception
from django.test import RequestFactory

import core.github_helper as github_helper


@pytest.fixture
def issue_reporting_env(tmp_path):
    base_dir = settings.BASE_DIR
    enabled = getattr(settings, "GITHUB_ISSUE_REPORTING_ENABLED", True)
    cooldown = getattr(settings, "GITHUB_ISSUE_REPORTING_COOLDOWN", 3600)

    settings.BASE_DIR = tmp_path
    settings.GITHUB_ISSUE_REPORTING_ENABLED = True
    settings.GITHUB_ISSUE_REPORTING_COOLDOWN = 3600

    try:
        yield tmp_path
    finally:
        settings.BASE_DIR = base_dir
        settings.GITHUB_ISSUE_REPORTING_ENABLED = enabled
        settings.GITHUB_ISSUE_REPORTING_COOLDOWN = cooldown


def _make_exception():
    try:
        raise ValueError("boom")
    except ValueError as exc:
        return exc


def _build_request():
    request = RequestFactory().get("/boom/")
    request.active_app = "core"
    return request


def test_exception_triggers_github_issue(monkeypatch, issue_reporting_env):
    calls: list[dict[str, object]] = []

    def fake_delay(payload):
        calls.append(payload)

    monkeypatch.setattr(github_helper.report_exception_to_github, "delay", fake_delay)

    User = get_user_model()
    user = User.objects.create_user(username="alice", password="secret")

    request = _build_request()
    request.user = user

    got_request_exception.send(
        sender=None, request=request, exception=_make_exception()
    )

    assert len(calls) == 1
    payload = calls[0]
    assert payload["path"] == "/boom/"
    assert payload["method"] == "GET"
    assert payload["user"] == "alice"
    assert payload["active_app"] == "core"
    assert payload["fingerprint"]
    assert payload["exception_class"].endswith("ValueError")
    assert "boom" in payload["traceback"]

    got_request_exception.send(
        sender=None, request=request, exception=_make_exception()
    )

    assert len(calls) == 1


def test_issue_reporting_can_be_disabled(monkeypatch, issue_reporting_env):
    settings.GITHUB_ISSUE_REPORTING_ENABLED = False
    calls: list[dict[str, object]] = []

    def fake_delay(payload):
        calls.append(payload)

    monkeypatch.setattr(github_helper.report_exception_to_github, "delay", fake_delay)

    request = _build_request()
    request.user = object()  # no authenticated user

    got_request_exception.send(
        sender=None, request=request, exception=_make_exception()
    )

    assert calls == []
