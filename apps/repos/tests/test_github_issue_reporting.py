"""Regression tests for automatic GitHub issue reporting feature gating."""

from __future__ import annotations

from pathlib import Path

import pytest
import requests
from django.core.signals import got_request_exception
from django.test import RequestFactory

from apps.features.models import Feature
from apps.repos.apps import (
    _configure_github_issue_reporting,
    queue_github_issue,
)
from apps.repos.issue_reporting import GITHUB_ISSUE_REPORTING_FEATURE_SLUG


class DummyResponse:
    def __init__(self, data, status_code: int = 201):
        self._data = data
        self.status_code = status_code
        self.closed = False

    def json(self):
        return self._data

    def close(self):
        self.closed = True


class DummyGitHubIssueClient:
    owner = "octo"
    repository = "demo"
    token = "token-1"


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


def _runtime_payload(**overrides):
    payload = {
        "path": "/boom",
        "method": "GET",
        "user": "anonymous",
        "active_app": "repos",
        "top_stack_frame": {
            "filename": "/srv/app/views.py",
            "lineno": 42,
            "name": "boom",
        },
        "fingerprint": "fingerprint-1",
        "exception_class": "builtins.ValueError",
        "traceback": "Traceback (most recent call last):\nValueError: boom\n",
    }
    payload.update(overrides)
    return payload


def _patch_active_issue_client(monkeypatch):
    from apps.repos.services import github as github_service

    monkeypatch.setattr(
        github_service.GitHubIssue,
        "from_active_repository",
        classmethod(lambda cls: DummyGitHubIssueClient()),
    )
    return github_service


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
    assert (
        Path(tmp_path) / ".locks" / "github-issues" / payload["fingerprint"]
    ).exists()


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


def test_signal_configuration_connects_runtime_gated_handler(
    db, monkeypatch, settings, tmp_path
):
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
        got_request_exception.disconnect(
            dispatch_uid="apps.repos.github_issue_reporter"
        )
        _configure_github_issue_reporting()

    assert len(enqueued) == 1


def test_already_queued_reports_still_run_after_feature_is_disabled(
    db, settings, monkeypatch
):
    """Queued reports should still execute even if the feature is later disabled."""

    settings.GITHUB_ISSUE_REPORTING_ENABLED = True
    _set_github_issue_reporting_feature(enabled=False)

    from apps.repos.tasks import report_exception_to_github

    github_service = _patch_active_issue_client(monkeypatch)
    calls: dict[str, object] = {}

    monkeypatch.setattr(github_service, "fetch_repository_issues", lambda **kwargs: [])

    def fake_create_issue(owner, repository, **kwargs):
        calls["owner"] = owner
        calls["repository"] = repository
        calls.update(kwargs)
        return DummyResponse({"html_url": "https://github.com/octo/demo/issues/12"})

    monkeypatch.setattr(github_service, "create_issue", fake_create_issue)

    issue_url = report_exception_to_github(
        _runtime_payload(fingerprint="queued-before-disable")
    )

    assert issue_url == "https://github.com/octo/demo/issues/12"
    assert calls["labels"] == ("automation", "bug", "priority: critical")


def test_report_exception_to_github_creates_labeled_issue(monkeypatch):
    """Enabled queued reports should create actionable labeled GitHub issues."""

    from apps.repos.tasks import report_exception_to_github

    github_service = _patch_active_issue_client(monkeypatch)
    calls: dict[str, object] = {}

    monkeypatch.setattr(github_service, "fetch_repository_issues", lambda **kwargs: [])

    def fake_create_issue(owner, repository, **kwargs):
        calls["owner"] = owner
        calls["repository"] = repository
        calls.update(kwargs)
        return DummyResponse({"html_url": "https://github.com/octo/demo/issues/13"})

    monkeypatch.setattr(github_service, "create_issue", fake_create_issue)

    issue_url = report_exception_to_github(_runtime_payload())

    assert issue_url == "https://github.com/octo/demo/issues/13"
    assert calls["owner"] == "octo"
    assert calls["repository"] == "demo"
    assert calls["token"] == "token-1"
    assert calls["title"] == "Runtime exception: builtins.ValueError at /boom"
    assert calls["labels"] == ("automation", "bug", "priority: critical")
    assert "<!-- runtime-exception-fingerprint:fingerprint-1 -->" in calls["body"]
    assert "Top stack frame: `/srv/app/views.py:42 in boom`" in calls["body"]
    assert "ValueError: boom" in calls["body"]
    assert "fingerprint" not in calls


def test_report_exception_to_github_retries_without_labels_when_unavailable(
    monkeypatch,
):
    """Missing repository labels should not block exception issue creation."""

    from apps.repos.tasks import report_exception_to_github

    github_service = _patch_active_issue_client(monkeypatch)
    labels_seen: list[object] = []

    monkeypatch.setattr(github_service, "fetch_repository_issues", lambda **kwargs: [])

    def fake_create_issue(owner, repository, **kwargs):
        labels_seen.append(kwargs.get("labels"))
        if len(labels_seen) == 1:
            response = requests.Response()
            response.status_code = 422
            response._content = b'{"message":"Label does not exist"}'
            raise requests.HTTPError("422 Client Error", response=response)
        return DummyResponse({"html_url": "https://github.com/octo/demo/issues/15"})

    monkeypatch.setattr(github_service, "create_issue", fake_create_issue)

    issue_url = report_exception_to_github(_runtime_payload())

    assert issue_url == "https://github.com/octo/demo/issues/15"
    assert labels_seen == [("automation", "bug", "priority: critical"), None]


def test_report_exception_to_github_retries_without_labels_after_empty_response(
    monkeypatch,
):
    """A label-related empty create response should still retry unlabeled."""

    from apps.repos.tasks import report_exception_to_github

    github_service = _patch_active_issue_client(monkeypatch)
    labels_seen: list[object] = []

    monkeypatch.setattr(github_service, "fetch_repository_issues", lambda **kwargs: [])

    def fake_create_issue(owner, repository, **kwargs):
        labels_seen.append(kwargs.get("labels"))
        if len(labels_seen) == 1:
            return None
        return DummyResponse({"html_url": "https://github.com/octo/demo/issues/16"})

    monkeypatch.setattr(github_service, "create_issue", fake_create_issue)

    issue_url = report_exception_to_github(_runtime_payload())

    assert issue_url == "https://github.com/octo/demo/issues/16"
    assert labels_seen == [("automation", "bug", "priority: critical"), None]


def test_report_exception_to_github_updates_existing_fingerprint_issue(monkeypatch):
    """Existing open fingerprint issues should receive an occurrence comment."""

    from apps.repos.tasks import report_exception_to_github

    github_service = _patch_active_issue_client(monkeypatch)
    marker = "<!-- runtime-exception-fingerprint:fingerprint-1 -->"
    comments: list[dict[str, object]] = []

    monkeypatch.setattr(
        github_service,
        "fetch_repository_issues",
        lambda **kwargs: [
            {
                "number": 33,
                "state": "open",
                "html_url": "https://github.com/octo/demo/issues/33",
                "body": f"Existing issue\n\n{marker}",
            }
        ],
    )
    monkeypatch.setattr(
        github_service,
        "create_issue",
        lambda **kwargs: pytest.fail("duplicate issue should not be created"),
    )

    def fake_comment(owner, repository, **kwargs):
        comments.append({"owner": owner, "repository": repository, **kwargs})
        return DummyResponse({"id": 1}, status_code=201)

    monkeypatch.setattr(github_service, "create_issue_comment", fake_comment)

    issue_url = report_exception_to_github(_runtime_payload())

    assert issue_url == "https://github.com/octo/demo/issues/33"
    assert comments[0]["owner"] == "octo"
    assert comments[0]["repository"] == "demo"
    assert comments[0]["issue_number"] == 33
    assert "Repeated runtime exception" in comments[0]["body"]


def test_report_exception_to_github_reopens_closed_fingerprint_issue(monkeypatch):
    """Closed fingerprint issues should reopen before receiving an occurrence comment."""

    from apps.repos.tasks import report_exception_to_github

    github_service = _patch_active_issue_client(monkeypatch)
    marker = "<!-- runtime-exception-fingerprint:fingerprint-1 -->"
    reopened: list[int] = []
    comments: list[int] = []
    states_seen: list[str] = []

    def fake_fetch_repository_issues(**kwargs):
        states_seen.append(kwargs["state"])
        if kwargs["state"] == "open":
            return []
        return [
            {
                "number": 34,
                "state": "closed",
                "html_url": "https://github.com/octo/demo/issues/34",
                "body": f"Existing issue\n\n{marker}",
            }
        ]

    monkeypatch.setattr(
        github_service, "fetch_repository_issues", fake_fetch_repository_issues
    )

    def fake_reopen(**kwargs):
        reopened.append(kwargs["issue_number"])
        return DummyResponse({"state": "open"}, status_code=200)

    def fake_comment(owner, repository, **kwargs):
        comments.append(kwargs["issue_number"])
        return DummyResponse({"id": 1}, status_code=201)

    monkeypatch.setattr(github_service, "reopen_issue", fake_reopen)
    monkeypatch.setattr(github_service, "create_issue_comment", fake_comment)

    issue_url = report_exception_to_github(_runtime_payload())

    assert issue_url == "https://github.com/octo/demo/issues/34"
    assert states_seen == ["open", "closed"]
    assert reopened == [34]
    assert comments == [34]


def test_report_exception_to_github_handles_token_failure(monkeypatch, caplog):
    """GitHub token failures should not escape the reporting task."""

    from apps.repos.services.github import GitHubIssue, GitHubRepositoryError
    from apps.repos.tasks import report_exception_to_github

    monkeypatch.setattr(
        GitHubIssue,
        "from_active_repository",
        classmethod(
            lambda cls: (_ for _ in ()).throw(GitHubRepositoryError("missing token"))
        ),
    )

    with caplog.at_level("WARNING"):
        issue_url = report_exception_to_github(_runtime_payload())

    assert issue_url is None
    assert "missing token" in caplog.text


def test_report_exception_to_github_redacts_sensitive_paths_and_users(monkeypatch):
    """Created issue payloads should not publish tokenized URLs or usernames."""

    from apps.repos.tasks import report_exception_to_github

    github_service = _patch_active_issue_client(monkeypatch)
    calls: dict[str, object] = {}

    monkeypatch.setattr(github_service, "fetch_repository_issues", lambda **kwargs: [])

    def fake_create_issue(owner, repository, **kwargs):
        calls["owner"] = owner
        calls["repository"] = repository
        calls.update(kwargs)
        return DummyResponse({"html_url": "https://github.com/octo/demo/issues/17"})

    monkeypatch.setattr(github_service, "create_issue", fake_create_issue)

    tracking_token = "550e8400-e29b-41d4-a716-446655440000"
    report_exception_to_github(
        _runtime_payload(
            path=f"/cards/register/{tracking_token}?ref=email#fragment",
            user="victim@example.com",
            traceback=(
                f"RuntimeError at /cards/register/{tracking_token}?ref=email#fragment\n"
                "client_secret=s3cr3t access_token=tok123 "
                "refresh_token=ref456 SECRET_KEY=django-secret\n"
            ),
        )
    )

    title = calls["title"]
    body = calls["body"]
    assert (
        title
        == "Runtime exception: builtins.ValueError at /cards/register/[REDACTED]?ref=email#fragment"
    )
    assert "- Path: `/cards/register/[REDACTED]?ref=email#fragment`" in body
    assert "- User: `[REDACTED]`" in body
    assert "client_secret=[REDACTED]" in body
    assert "access_token=[REDACTED]" in body
    assert "refresh_token=[REDACTED]" in body
    assert "SECRET_KEY=[REDACTED]" in body
    assert tracking_token not in title
    assert tracking_token not in body
    assert "victim@example.com" not in body
    assert "s3cr3t" not in body
    assert "tok123" not in body
    assert "ref456" not in body
    assert "django-secret" not in body


def test_report_exception_to_github_redacts_sensitive_path_query(monkeypatch):
    """Request path query parameters should not bypass secret redaction."""

    from apps.repos.tasks import report_exception_to_github

    github_service = _patch_active_issue_client(monkeypatch)
    calls: dict[str, object] = {}

    monkeypatch.setattr(github_service, "fetch_repository_issues", lambda **kwargs: [])

    def fake_create_issue(owner, repository, **kwargs):
        calls["owner"] = owner
        calls["repository"] = repository
        calls.update(kwargs)
        return DummyResponse({"html_url": "https://github.com/octo/demo/issues/19"})

    monkeypatch.setattr(github_service, "create_issue", fake_create_issue)

    report_exception_to_github(
        _runtime_payload(
            path="/reset?token=secret-token&client_secret=client-secret&ok=value",
        )
    )

    title = calls["title"]
    body = calls["body"]
    assert "token=[REDACTED]" in title
    assert "client_secret=[REDACTED]" in title
    assert "ok=value" in title
    assert "token=[REDACTED]" in body
    assert "client_secret=[REDACTED]" in body
    assert "ok=value" in body
    assert "secret-token" not in title
    assert "client-secret" not in title
    assert "secret-token" not in body
    assert "client-secret" not in body


def test_report_exception_to_github_redacts_sensitive_paths_in_comments(monkeypatch):
    """Repeated exception comments should not publish tokenized URLs or usernames."""

    from apps.repos.tasks import report_exception_to_github

    github_service = _patch_active_issue_client(monkeypatch)
    marker = "<!-- runtime-exception-fingerprint:fingerprint-1 -->"
    comments: list[dict[str, object]] = []

    monkeypatch.setattr(
        github_service,
        "fetch_repository_issues",
        lambda **kwargs: [
            {
                "number": 35,
                "state": "open",
                "html_url": "https://github.com/octo/demo/issues/35",
                "body": f"Existing issue\n\n{marker}",
            }
        ],
    )
    monkeypatch.setattr(
        github_service,
        "create_issue",
        lambda **kwargs: pytest.fail("duplicate issue should not be created"),
    )

    def fake_comment(owner, repository, **kwargs):
        comments.append({"owner": owner, "repository": repository, **kwargs})
        return DummyResponse({"id": 1}, status_code=201)

    monkeypatch.setattr(github_service, "create_issue_comment", fake_comment)

    invitation_token = "set-password-token-abc123"
    report_exception_to_github(
        _runtime_payload(
            path=f"/invitation/MQ/{invitation_token}?ref=email#fragment",
            user="victim@example.com",
        )
    )

    body = comments[0]["body"]
    assert "- Path: `/invitation/[REDACTED]/[REDACTED]?ref=email#fragment`" in body
    assert "- User: `[REDACTED]`" in body
    assert invitation_token not in body
    assert "victim@example.com" not in body


def test_report_exception_to_github_redacts_sensitive_traceback(monkeypatch):
    """Created issue bodies should redact common secret patterns."""

    from apps.repos.tasks import report_exception_to_github

    github_service = _patch_active_issue_client(monkeypatch)
    calls: dict[str, object] = {}

    monkeypatch.setattr(github_service, "fetch_repository_issues", lambda **kwargs: [])

    def fake_create_issue(owner, repository, **kwargs):
        calls["owner"] = owner
        calls["repository"] = repository
        calls.update(kwargs)
        return DummyResponse({"html_url": "https://github.com/octo/demo/issues/14"})

    monkeypatch.setattr(github_service, "create_issue", fake_create_issue)

    report_exception_to_github(
        _runtime_payload(
            traceback=(
                "Authorization: Bearer abc123\n"
                "Authorization: token ghp_secret\n"
                "Cookie: sessionid=abc; csrftoken=def\n"
                "password=hunter2\n"
                "token=secret-token\n"
                "client_secret=s3cr3t access_token=tok123 "
                "refresh_token=ref456 SECRET_KEY=django-secret\n"
                'client_secret = "my secret token"\n'
                "refresh_token = 'secret token value'\n"
                "SECRET_KEY=$abc\n"
                "SECRET_KEY=django-insecure-abc$def\n"
                "access_token=abc{def}#ghi*jkl\n"
            )
        )
    )

    body = calls["body"]
    assert "Authorization: [REDACTED]" in body
    assert "Cookie: [REDACTED]" in body
    assert "password=[REDACTED]" in body
    assert "token=[REDACTED]" in body
    assert "client_secret=[REDACTED]" in body
    assert "access_token=[REDACTED]" in body
    assert "refresh_token=[REDACTED]" in body
    assert "SECRET_KEY=[REDACTED]" in body
    assert "client_secret = [REDACTED]" in body
    assert "refresh_token = [REDACTED]" in body
    assert "abc123" not in body
    assert "ghp_secret" not in body
    assert "sessionid=abc" not in body
    assert "csrftoken=def" not in body
    assert "hunter2" not in body
    assert "secret-token" not in body
    assert "s3cr3t" not in body
    assert "tok123" not in body
    assert "ref456" not in body
    assert "django-secret" not in body
    assert "my secret token" not in body
    assert "secret token value" not in body
    assert "$abc" not in body
    assert "django-insecure-abc$def" not in body
    assert "abc{def}#ghi*jkl" not in body


def test_report_exception_to_github_preserves_ordinary_source_paths(monkeypatch):
    """Source paths in tracebacks should remain actionable after redaction."""

    from apps.repos.tasks import report_exception_to_github

    github_service = _patch_active_issue_client(monkeypatch)
    calls: dict[str, object] = {}

    monkeypatch.setattr(github_service, "fetch_repository_issues", lambda **kwargs: [])

    def fake_create_issue(owner, repository, **kwargs):
        calls["owner"] = owner
        calls["repository"] = repository
        calls.update(kwargs)
        return DummyResponse({"html_url": "https://github.com/octo/demo/issues/18"})

    monkeypatch.setattr(github_service, "create_issue", fake_create_issue)

    report_exception_to_github(
        _runtime_payload(
            top_stack_frame={
                "filename": "/srv/app/apps/repos/github_monitor.py",
                "lineno": 42,
                "name": "test_github_issue_reporting",
            },
            traceback=(
                'File "/srv/app/apps/repos/tests/test_github_issue_reporting.py", '
                "line 17, in test_github_issue_reporting\n"
                'File "/srv/app/apps/repos/github_monitor.py", line 44, in run\n'
            ),
        )
    )

    body = calls["body"]
    assert "/srv/app/apps/repos/github_monitor.py:42" in body
    assert "test_github_issue_reporting.py" in body
    assert "github_monitor.py" in body
