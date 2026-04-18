"""Tests for Release Management GitHub operation routing."""

from __future__ import annotations

from typing import Any

import pytest

from apps.repos.release_management import (
    EXECUTION_MODE_BINARY,
    EXECUTION_MODE_SUITE,
    MERGE_METHOD_MERGE,
    MERGE_METHOD_SQUASH,
    ReleaseManagementClient,
    ReleaseManagementError,
    RepositoryRef,
)


@pytest.mark.django_db
@pytest.mark.critical
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


@pytest.mark.django_db
def test_release_management_uses_feature_mode_when_mode_not_explicit(monkeypatch):
    """Client should honor feature metadata mode when explicit mode is omitted."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "_feature_mode",
        staticmethod(lambda: EXECUTION_MODE_BINARY),
    )
    monkeypatch.setattr(
        ReleaseManagementClient,
        "_resolve_token",
        lambda self: "token-3",
    )

    called: dict[str, Any] = {}

    def fake_gh(self, args: list[str]) -> list[dict[str, Any]]:
        called["args"] = args
        return [{"number": 5, "title": "Feature mode", "state": "open"}]

    monkeypatch.setattr(ReleaseManagementClient, "_run_gh_json", fake_gh)

    client = ReleaseManagementClient()
    rows = client.list_issues(RepositoryRef(owner="octo", name="demo"))

    assert rows[0]["number"] == 5
    assert called["args"][0:2] == ["issue", "list"]


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


@pytest.mark.django_db
def test_release_management_comments_issue_via_suite_api(monkeypatch):
    """Issue comments should use suite API helpers when suite mode is available."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "_resolve_token",
        lambda self: "token-1",
    )

    called: dict[str, Any] = {}

    def fail_gh(self, args: list[str]) -> str:  # pragma: no cover - explicit failure branch
        raise AssertionError(f"gh fallback should not run, got {args}")

    monkeypatch.setattr(ReleaseManagementClient, "_run_gh", fail_gh)

    from apps.repos.services import github as github_service

    def fake_comment_issue(owner, name, *, issue_number, token, body):
        called.update(
            {
                "owner": owner,
                "name": name,
                "issue_number": issue_number,
                "token": token,
                "body": body,
            }
        )

    monkeypatch.setattr(github_service, "create_issue_comment", fake_comment_issue)

    client = ReleaseManagementClient()
    client.comment_issue(
        RepositoryRef(owner="octo", name="demo"),
        number=23,
        body="Please attach logs.",
    )

    assert called == {
        "owner": "octo",
        "name": "demo",
        "issue_number": 23,
        "token": "token-1",
        "body": "Please attach logs.",
    }


@pytest.mark.django_db
def test_release_management_closes_issue_via_gh_when_binary_mode(monkeypatch):
    """Binary mode should close issues via gh."""

    called: dict[str, Any] = {}

    def fake_gh(self, args: list[str]) -> str:
        called["args"] = args
        return ""

    monkeypatch.setattr(ReleaseManagementClient, "_run_gh", fake_gh)

    client = ReleaseManagementClient(mode=EXECUTION_MODE_BINARY)
    client.close_issue(RepositoryRef(owner="octo", name="demo"), number=24)

    assert called["args"] == ["issue", "close", "24", "--repo", "octo/demo"]


@pytest.mark.django_db
def test_release_management_marks_pull_request_ready_via_suite_api(monkeypatch):
    """Ready-for-review should use suite API helpers when available."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "_resolve_token",
        lambda self: "token-1",
    )

    called: dict[str, Any] = {}

    def fail_gh(self, args: list[str]) -> str:  # pragma: no cover - explicit failure branch
        raise AssertionError(f"gh fallback should not run, got {args}")

    monkeypatch.setattr(ReleaseManagementClient, "_run_gh", fail_gh)

    from apps.repos.services import github as github_service

    def fake_ready(owner, name, *, pull_number, token):
        called.update(
            {
                "owner": owner,
                "name": name,
                "pull_number": pull_number,
                "token": token,
            }
        )

    monkeypatch.setattr(github_service, "mark_pull_request_ready", fake_ready)

    client = ReleaseManagementClient()
    client.mark_pull_request_ready(RepositoryRef(owner="octo", name="demo"), number=31)

    assert called == {
        "owner": "octo",
        "name": "demo",
        "pull_number": 31,
        "token": "token-1",
    }


@pytest.mark.django_db
def test_release_management_merges_pull_request_via_gh_with_selected_method(monkeypatch):
    """Binary merge should forward the selected gh merge flag."""

    called: dict[str, Any] = {}

    def fake_gh(self, args: list[str]) -> str:
        called["args"] = args
        return ""

    monkeypatch.setattr(ReleaseManagementClient, "_run_gh", fake_gh)

    client = ReleaseManagementClient(mode=EXECUTION_MODE_BINARY)
    client.merge_pull_request(
        RepositoryRef(owner="octo", name="demo"),
        number=77,
        merge_method=MERGE_METHOD_SQUASH,
    )

    assert called["args"] == [
        "pr",
        "merge",
        "77",
        "--repo",
        "octo/demo",
        "--squash",
    ]


@pytest.mark.django_db
def test_release_management_rejects_invalid_merge_method():
    """Unsupported merge methods should raise a release-management error."""

    client = ReleaseManagementClient(mode=EXECUTION_MODE_BINARY)

    with pytest.raises(ReleaseManagementError, match="Unsupported merge method"):
        client.merge_pull_request(
            RepositoryRef(owner="octo", name="demo"),
            number=77,
            merge_method="invalid",
        )


@pytest.mark.django_db
def test_release_management_defaults_merge_method_to_merge(monkeypatch):
    """Omitted merge methods should default to a normal merge."""

    called: dict[str, Any] = {}

    def fake_gh(self, args: list[str]) -> str:
        called["args"] = args
        return ""

    monkeypatch.setattr(ReleaseManagementClient, "_run_gh", fake_gh)

    client = ReleaseManagementClient(mode=EXECUTION_MODE_BINARY)
    client.merge_pull_request(RepositoryRef(owner="octo", name="demo"), number=88)

    assert called["args"][-1] == f"--{MERGE_METHOD_MERGE}"


@pytest.mark.django_db
def test_release_management_lists_issue_activity_with_reactions(monkeypatch):
    """Issue activity should normalize reactions into icon-rich summaries."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "_resolve_token",
        lambda self: "token-1",
    )

    from apps.repos.services import github as github_service

    monkeypatch.setattr(
        github_service,
        "fetch_issue_comments",
        lambda **kwargs: [
            {
                "id": 91,
                "body": "Looking now",
                "created_at": "2026-04-17T21:00:00Z",
                "html_url": "https://example.com/issues/12#issuecomment-91",
                "user": {"login": "reviewer-1"},
            }
        ],
    )
    monkeypatch.setattr(
        github_service,
        "fetch_issue_comment_reactions",
        lambda **kwargs: [
            {"content": "eyes", "user": {"login": "reviewer-2"}},
            {"content": "+1", "user": {"login": "reviewer-3"}},
        ],
    )

    client = ReleaseManagementClient()
    activity = client.list_issue_activity(RepositoryRef(owner="octo", name="demo"), number=12)

    assert activity[0]["author_name"] == "reviewer-1"
    assert activity[0]["reactions"][0]["display"] == "👀 reviewer-2"
    assert activity[0]["reactions"][1]["display"] == "👍 reviewer-3"


@pytest.mark.django_db
def test_release_management_wraps_suite_errors_for_issue_activity_reactions(monkeypatch):
    """Issue activity should normalize reaction fetch errors in suite mode."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "_resolve_token",
        lambda self: "token-1",
    )

    from apps.repos.services import github as github_service

    monkeypatch.setattr(
        github_service,
        "fetch_issue_comments",
        lambda **kwargs: [{"id": 91, "body": "Looking now", "user": {"login": "reviewer-1"}}],
    )
    monkeypatch.setattr(
        github_service,
        "fetch_issue_comment_reactions",
        lambda **kwargs: (_ for _ in ()).throw(
            github_service.GitHubRepositoryError("reactions denied")
        ),
    )

    client = ReleaseManagementClient()
    with pytest.raises(ReleaseManagementError, match="reactions denied"):
        client.list_issue_activity(RepositoryRef(owner="octo", name="demo"), number=12)


@pytest.mark.django_db
def test_release_management_lists_pull_request_activity_with_gh_api(monkeypatch):
    """Binary activity should flatten gh api pages and sort issue/review comments."""

    def fake_api(self, endpoint: str) -> list[dict[str, Any]]:
        if endpoint == "repos/octo/demo/issues/14/comments?per_page=100":
            return [
                {
                    "id": 100,
                    "body": "Top-level note",
                    "created_at": "2026-04-17T21:00:00Z",
                    "user": {"login": "reviewer-1"},
                }
            ]
        if endpoint == "repos/octo/demo/pulls/14/comments?per_page=100":
            return [
                {
                    "id": 101,
                    "body": "Inline note",
                    "created_at": "2026-04-17T21:01:00Z",
                    "path": "apps/repos/admin.py",
                    "line": 42,
                    "user": {"login": "reviewer-2"},
                }
            ]
        if endpoint == "repos/octo/demo/issues/comments/100/reactions?per_page=100":
            return [{"content": "eyes", "user": {"login": "reviewer-3"}}]
        if endpoint == "repos/octo/demo/pulls/comments/101/reactions?per_page=100":
            return [{"content": "rocket", "user": {"login": "reviewer-4"}}]
        raise AssertionError(f"unexpected endpoint {endpoint}")

    monkeypatch.setattr(ReleaseManagementClient, "_run_gh_api_items", fake_api)

    client = ReleaseManagementClient(mode=EXECUTION_MODE_BINARY)
    activity = client.list_pull_request_activity(
        RepositoryRef(owner="octo", name="demo"),
        number=14,
    )

    assert [item["id"] for item in activity] == [100, 101]
    assert activity[0]["reactions"][0]["display"] == "👀 reviewer-3"
    assert activity[1]["path"] == "apps/repos/admin.py"
    assert activity[1]["reactions"][0]["display"] == "🚀 reviewer-4"


@pytest.mark.django_db
def test_release_management_wraps_suite_errors_for_pull_request_activity_reactions(monkeypatch):
    """Pull-request activity should normalize reaction fetch errors in suite mode."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "_resolve_token",
        lambda self: "token-1",
    )

    from apps.repos.services import github as github_service

    monkeypatch.setattr(
        github_service,
        "fetch_issue_comments",
        lambda **kwargs: [{"id": 100, "body": "Top-level note", "user": {"login": "reviewer-1"}}],
    )
    monkeypatch.setattr(
        github_service,
        "fetch_pull_request_review_comments",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        github_service,
        "fetch_issue_comment_reactions",
        lambda **kwargs: (_ for _ in ()).throw(
            github_service.GitHubRepositoryError("comment reactions denied")
        ),
    )

    client = ReleaseManagementClient()
    with pytest.raises(ReleaseManagementError, match="comment reactions denied"):
        client.list_pull_request_activity(RepositoryRef(owner="octo", name="demo"), number=14)


@pytest.mark.django_db
def test_release_management_wraps_suite_errors_for_issue_operations(monkeypatch):
    """Suite issue operations should normalize service exceptions."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "_resolve_token",
        lambda self: "token-1",
    )

    from apps.repos.services import github as github_service

    monkeypatch.setattr(
        github_service,
        "create_issue_comment",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            github_service.GitHubRepositoryError("permission denied")
        ),
    )

    client = ReleaseManagementClient()
    with pytest.raises(ReleaseManagementError, match="permission denied"):
        client.comment_issue(RepositoryRef(owner="octo", name="demo"), number=10, body="note")


@pytest.mark.django_db
def test_release_management_wraps_suite_errors_for_pull_request_operations(monkeypatch):
    """Suite PR operations should normalize service exceptions."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "_resolve_token",
        lambda self: "token-1",
    )

    from apps.repos.services import github as github_service

    monkeypatch.setattr(
        github_service,
        "merge_pull_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            github_service.GitHubRepositoryError("merge conflict")
        ),
    )

    client = ReleaseManagementClient()
    with pytest.raises(ReleaseManagementError, match="merge conflict"):
        client.merge_pull_request(RepositoryRef(owner="octo", name="demo"), number=11)


@pytest.mark.django_db
def test_release_management_get_issue_uses_gh_issue_view(monkeypatch):
    """Issue lookups should query gh issue view directly."""

    captured: dict[str, Any] = {}

    def fake_gh_json(self, args: list[str]) -> dict[str, Any]:
        captured["args"] = args
        return {"number": 77, "state": "open", "title": "Older issue", "url": "https://example.com/77"}

    monkeypatch.setattr(ReleaseManagementClient, "_run_gh_json", fake_gh_json)

    client = ReleaseManagementClient(mode=EXECUTION_MODE_BINARY)
    issue = client.get_issue(RepositoryRef(owner="octo", name="demo"), number=77)

    assert issue is not None
    assert issue["number"] == 77
    assert captured["args"][0:2] == ["issue", "view"]


@pytest.mark.django_db
def test_release_management_get_pull_request_uses_gh_pr_view(monkeypatch):
    """Pull-request lookups should query gh pr view directly."""

    captured: dict[str, Any] = {}

    def fake_gh_json(self, args: list[str]) -> dict[str, Any]:
        captured["args"] = args
        return {
            "number": 88,
            "state": "open",
            "title": "Older PR",
            "url": "https://example.com/88",
            "isDraft": False,
        }

    monkeypatch.setattr(ReleaseManagementClient, "_run_gh_json", fake_gh_json)

    client = ReleaseManagementClient(mode=EXECUTION_MODE_BINARY)
    pull_request = client.get_pull_request(RepositoryRef(owner="octo", name="demo"), number=88)

    assert pull_request is not None
    assert pull_request["number"] == 88
    assert captured["args"][0:2] == ["pr", "view"]
