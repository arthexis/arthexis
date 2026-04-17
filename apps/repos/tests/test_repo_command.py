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
