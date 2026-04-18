from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from apps.repos.release_management import ReleaseManagementClient, RepositoryRef


@pytest.mark.django_db
def test_repo_command_comments_on_issue(monkeypatch):
    """The repo command should dispatch issue comments through the client."""

    captured: dict[str, object] = {}

    def fake_comment_issue(self, repository: RepositoryRef, *, number: int, body: str) -> None:
        captured["repository"] = repository
        captured["number"] = number
        captured["body"] = body

    monkeypatch.setattr(
        ReleaseManagementClient,
        "comment_issue",
        fake_comment_issue,
    )

    stdout = StringIO()
    call_command(
        "repo",
        "issues",
        "comment",
        "123",
        "--body",
        "Please attach logs.",
        "--repo",
        "octo/demo",
        stdout=stdout,
    )

    assert captured["repository"] == RepositoryRef(owner="octo", name="demo")
    assert captured["number"] == 123
    assert captured["body"] == "Please attach logs."
    assert "Comment added to issue #123" in stdout.getvalue()


@pytest.mark.django_db
def test_repo_command_marks_pull_request_ready(monkeypatch):
    """The repo command should expose the ready-for-review PR action."""

    captured: dict[str, object] = {}

    def fake_ready(self, repository: RepositoryRef, *, number: int) -> None:
        captured["repository"] = repository
        captured["number"] = number

    monkeypatch.setattr(
        ReleaseManagementClient,
        "mark_pull_request_ready",
        fake_ready,
    )

    stdout = StringIO()
    call_command(
        "repo",
        "prs",
        "ready",
        "456",
        "octo/demo",
        stdout=stdout,
    )

    assert captured["repository"] == RepositoryRef(owner="octo", name="demo")
    assert captured["number"] == 456
    assert "Pull request #456 is ready for review" in stdout.getvalue()


@pytest.mark.django_db
def test_repo_command_merges_pull_request_with_selected_method(monkeypatch):
    """The repo command should pass the selected merge method through."""

    captured: dict[str, object] = {}

    def fake_merge(
        self,
        repository: RepositoryRef,
        *,
        number: int,
        merge_method: str,
    ) -> None:
        captured["repository"] = repository
        captured["number"] = number
        captured["merge_method"] = merge_method

    monkeypatch.setattr(
        ReleaseManagementClient,
        "merge_pull_request",
        fake_merge,
    )

    stdout = StringIO()
    call_command(
        "repo",
        "prs",
        "merge",
        "789",
        "--method",
        "squash",
        "--repo",
        "octo/demo",
        stdout=stdout,
    )

    assert captured["repository"] == RepositoryRef(owner="octo", name="demo")
    assert captured["number"] == 789
    assert captured["merge_method"] == "squash"
    assert "Pull request #789 merged with squash" in stdout.getvalue()


@pytest.mark.django_db
def test_repo_command_shows_pull_request_activity_with_reactions(monkeypatch):
    """The repo command should print reviewer reaction icons in PR activity output."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "get_pull_request",
        lambda self, repository, number: {
            "number": number,
            "state": "open",
            "title": "Monitoring surface",
            "url": f"https://github.com/octo/demo/pull/{number}",
            "isDraft": False,
        },
    )
    monkeypatch.setattr(
        ReleaseManagementClient,
        "list_pull_request_activity",
        lambda self, repository, number: [
            {
                "kind_label": "Review comment",
                "author_name": "reviewer-1",
                "created_at": "2026-04-17T21:00:00Z",
                "path": "apps/repos/admin.py",
                "line": 44,
                "reactions": [{"display": "👀 reviewer-2"}],
                "body": "I am looking at this.",
            }
        ],
    )

    stdout = StringIO()
    call_command(
        "repo",
        "prs",
        "show",
        "456",
        "--repo",
        "octo/demo",
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert "Pull request #456 [open] Monitoring surface" in output
    assert "Review comment by reviewer-1" in output
    assert "apps/repos/admin.py:44" in output
    assert "👀 reviewer-2" in output


@pytest.mark.django_db
def test_repo_command_supports_top_level_repo_option(monkeypatch):
    """The repo command should keep compatibility with top-level --repo usage."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "list_issues",
        lambda self, repository, state="open": [
            {"number": 1, "state": state, "title": "Issue from top-level repo"}
        ],
    )

    stdout = StringIO()
    call_command(
        "repo",
        "--repo",
        "octo/demo",
        "issues",
        "list",
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert "#1 [open] Issue from top-level repo" in output
