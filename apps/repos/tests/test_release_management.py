"""Tests for Release Management GitHub operation routing."""

from __future__ import annotations

from typing import Any

import pytest

from apps.repos.release_management import (
    EXECUTION_MODE_BINARY,
    EXECUTION_MODE_SUITE,
    ReleaseManagementClient,
    RepositoryRef,
)


@pytest.mark.django_db
def test_release_management_uses_suite_api_by_default(monkeypatch):
    """Default suite mode should use API calls when token resolution succeeds."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "_feature_mode",
        staticmethod(lambda: EXECUTION_MODE_SUITE),
    )
    monkeypatch.setattr(
        ReleaseManagementClient,
        "_resolve_token",
        lambda self: "token-1",
    )

    def fail_gh(self, args: list[str]) -> Any:  # pragma: no cover - explicit failure branch
        raise AssertionError(f"gh fallback should not run, got {args}")

    monkeypatch.setattr(ReleaseManagementClient, "_run_gh_json", fail_gh)

    from apps.repos.services import github as github_service

    monkeypatch.setattr(
        github_service,
        "fetch_repository_issues",
        lambda **kwargs: [{"number": 7, "title": "Issue", "state": "open"}],
    )

    client = ReleaseManagementClient()
    rows = client.list_issues(RepositoryRef(owner="octo", name="demo"))

    assert len(rows) == 1
    assert rows[0]["number"] == 7


@pytest.mark.django_db
def test_release_management_binary_mode_prefers_gh(monkeypatch):
    """Binary mode should route list operations through gh regardless of token."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "_resolve_token",
        lambda self: "token-2",
    )

    called: dict[str, Any] = {}

    def fake_gh(self, args: list[str]) -> list[dict[str, Any]]:
        called["args"] = args
        return [{"number": 11, "title": "PR", "state": "open"}]

    monkeypatch.setattr(ReleaseManagementClient, "_run_gh_json", fake_gh)

    client = ReleaseManagementClient(mode=EXECUTION_MODE_BINARY)
    rows = client.list_pull_requests(RepositoryRef(owner="octo", name="demo"))

    assert rows[0]["number"] == 11
    assert called["args"][0:2] == ["pr", "list"]


@pytest.mark.django_db
def test_release_create_uses_double_dash_before_tag(monkeypatch):
    """Release creation should prevent option injection through tag values."""

    captured: dict[str, Any] = {}

    def fake_run(self, args: list[str]) -> str:
        captured["args"] = args
        return "ok"

    monkeypatch.setattr(ReleaseManagementClient, "_run_gh", fake_run)

    client = ReleaseManagementClient(mode=EXECUTION_MODE_BINARY)
    result = client.create_release(
        RepositoryRef(owner="octo", name="demo"),
        tag="-nasty",
        title="v1",
        notes="notes",
    )

    assert result == "ok"
    assert captured["args"][0:2] == ["release", "create"]
    assert captured["args"][-2:] == ["--", "-nasty"]


@pytest.mark.django_db
def test_release_management_prefers_suite_token_before_environment(monkeypatch):
    """Token resolution should prefer suite-managed token over env vars."""

    monkeypatch.setenv("GITHUB_TOKEN", "env-token")

    from apps.repos.services import github as github_service

    monkeypatch.setattr(github_service, "get_github_issue_token", lambda: "suite-token")

    client = ReleaseManagementClient()

    assert client._resolve_token() == "suite-token"


@pytest.mark.django_db
def test_release_management_disabled_feature_forces_gh_fallback(monkeypatch):
    """Disabled feature should prevent suite API routing even when token exists."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "_resolve_token",
        lambda self: "token-1",
    )
    monkeypatch.setattr(
        ReleaseManagementClient,
        "_feature_enabled",
        staticmethod(lambda: False),
    )

    called: dict[str, Any] = {}

    def fake_gh(self, args: list[str]) -> list[dict[str, Any]]:
        called["args"] = args
        return [{"number": 9, "title": "Fallback issue", "state": "open"}]

    def fail_suite(**kwargs):  # pragma: no cover - explicit failure branch
        raise AssertionError(f"suite API should not run, got {kwargs}")

    monkeypatch.setattr(ReleaseManagementClient, "_run_gh_json", fake_gh)

    from apps.repos.services import github as github_service

    monkeypatch.setattr(github_service, "fetch_repository_issues", fail_suite)

    client = ReleaseManagementClient()
    rows = client.list_issues(RepositoryRef(owner="octo", name="demo"))

    assert rows[0]["number"] == 9
    assert called["args"][0:2] == ["issue", "list"]


@pytest.mark.django_db
def test_release_management_normalizes_suite_issue_payload(monkeypatch):
    """Suite issue payloads should expose gh-style author and url keys."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "_resolve_token",
        lambda self: "token-1",
    )

    from apps.repos.services import github as github_service

    monkeypatch.setattr(
        github_service,
        "fetch_repository_issues",
        lambda **kwargs: [
            {
                "number": 12,
                "state": "open",
                "title": "Issue",
                "html_url": "https://example.com/issues/12",
                "user": {"login": "octocat"},
            }
        ],
    )

    client = ReleaseManagementClient()
    rows = client.list_issues(RepositoryRef(owner="octo", name="demo"))

    assert rows[0]["url"] == "https://example.com/issues/12"
    assert rows[0]["author"]["login"] == "octocat"


@pytest.mark.django_db
def test_release_management_normalizes_suite_pull_request_payload(monkeypatch):
    """Suite pull-request payloads should expose gh-style isDraft key."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "_resolve_token",
        lambda self: "token-1",
    )

    from apps.repos.services import github as github_service

    monkeypatch.setattr(
        github_service,
        "fetch_repository_pull_requests",
        lambda **kwargs: [
            {"number": 14, "state": "open", "title": "PR", "draft": True, "url": "https://example.com/pr/14"}
        ],
    )

    client = ReleaseManagementClient()
    rows = client.list_pull_requests(RepositoryRef(owner="octo", name="demo"))

    assert rows[0]["isDraft"] is True
