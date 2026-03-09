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
def test_release_management_falls_back_to_gh_when_token_missing(monkeypatch):
    """Suite mode should fallback to gh when no token is available."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "_feature_mode",
        staticmethod(lambda: EXECUTION_MODE_SUITE),
    )
    monkeypatch.setattr(
        ReleaseManagementClient,
        "_resolve_token",
        lambda self: None,
    )

    called: dict[str, Any] = {}

    def fake_gh(self, args: list[str]) -> list[dict[str, Any]]:
        called["args"] = args
        return [{"number": 3, "title": "Fallback issue", "state": "open"}]

    monkeypatch.setattr(ReleaseManagementClient, "_run_gh_json", fake_gh)

    client = ReleaseManagementClient()
    rows = client.list_issues(RepositoryRef(owner="octo", name="demo"))

    assert rows[0]["number"] == 3
    assert called["args"][0:2] == ["issue", "list"]


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
