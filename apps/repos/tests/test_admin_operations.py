"""Tests for GitHub issue and pull-request admin operations."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.repos.models.issues import RepositoryIssue, RepositoryPullRequest
from apps.repos.models.repositories import GitHubRepository
from apps.repos.release_management import RepositoryRef


def _admin_user():
    return get_user_model().objects.create_superuser(
        username="repos-admin",
        email="repos-admin@example.com",
        password="password123",
    )


def _repository() -> GitHubRepository:
    return GitHubRepository.objects.create(owner="arthexis", name="arthexis")


def test_repository_issue_comment_view_posts_comment(client, db, monkeypatch):
    user = _admin_user()
    client.force_login(user)

    repository = _repository()
    issue = RepositoryIssue.objects.create(
        repository=repository,
        number=104,
        title="Comment check",
        state="open",
        html_url="https://github.com/arthexis/arthexis/issues/104",
        created_at=timezone.now(),
        updated_at=timezone.now(),
    )
    old_updated_at = issue.updated_at
    captured: dict[str, object] = {}

    def fake_comment_issue(self, repository: RepositoryRef, *, number: int, body: str) -> None:
        captured["repository"] = repository
        captured["number"] = number
        captured["body"] = body

    monkeypatch.setattr(
        "apps.repos.admin.ReleaseManagementClient.comment_issue",
        fake_comment_issue,
    )

    response = client.post(
        reverse("admin:repos_repositoryissue_comment", args=[issue.pk]),
        {"body": "Please add a reproduction."},
        follow=True,
    )

    assert response.status_code == 200
    issue.refresh_from_db()
    assert captured["repository"] == RepositoryRef(owner="arthexis", name="arthexis")
    assert captured["number"] == 104
    assert captured["body"] == "Please add a reproduction."
    assert issue.updated_at >= old_updated_at


def test_repository_issue_close_view_closes_issue(client, db, monkeypatch):
    user = _admin_user()
    client.force_login(user)

    repository = _repository()
    issue = RepositoryIssue.objects.create(
        repository=repository,
        number=105,
        title="Close check",
        state="open",
        html_url="https://github.com/arthexis/arthexis/issues/105",
        created_at=timezone.now(),
        updated_at=timezone.now(),
    )
    captured: dict[str, object] = {}

    def fake_close_issue(self, repository: RepositoryRef, *, number: int) -> None:
        captured["repository"] = repository
        captured["number"] = number

    monkeypatch.setattr(
        "apps.repos.admin.ReleaseManagementClient.close_issue",
        fake_close_issue,
    )

    response = client.post(
        reverse("admin:repos_repositoryissue_close", args=[issue.pk]),
        follow=True,
    )

    assert response.status_code == 200
    issue.refresh_from_db()
    assert captured["repository"] == RepositoryRef(owner="arthexis", name="arthexis")
    assert captured["number"] == 105
    assert issue.state == "closed"


def test_repository_pull_request_ready_view_marks_ready(client, db, monkeypatch):
    user = _admin_user()
    client.force_login(user)

    repository = _repository()
    pr = RepositoryPullRequest.objects.create(
        repository=repository,
        number=106,
        title="Ready check",
        state="open",
        is_draft=True,
        html_url="https://github.com/arthexis/arthexis/pull/106",
        created_at=timezone.now(),
        updated_at=timezone.now(),
    )
    captured: dict[str, object] = {}

    def fake_ready(self, repository: RepositoryRef, *, number: int) -> None:
        captured["repository"] = repository
        captured["number"] = number

    monkeypatch.setattr(
        "apps.repos.admin.ReleaseManagementClient.mark_pull_request_ready",
        fake_ready,
    )

    response = client.post(
        reverse("admin:repos_repositorypullrequest_ready", args=[pr.pk]),
        follow=True,
    )

    assert response.status_code == 200
    pr.refresh_from_db()
    assert captured["repository"] == RepositoryRef(owner="arthexis", name="arthexis")
    assert captured["number"] == 106
    assert pr.is_draft is False


def test_repository_pull_request_merge_view_merges_pull_request(client, db, monkeypatch):
    user = _admin_user()
    client.force_login(user)

    repository = _repository()
    pr = RepositoryPullRequest.objects.create(
        repository=repository,
        number=107,
        title="Merge check",
        state="open",
        is_draft=False,
        html_url="https://github.com/arthexis/arthexis/pull/107",
        created_at=timezone.now(),
        updated_at=timezone.now(),
    )
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
        "apps.repos.admin.ReleaseManagementClient.merge_pull_request",
        fake_merge,
    )

    response = client.post(
        reverse("admin:repos_repositorypullrequest_merge", args=[pr.pk]),
        {"merge_method": "squash"},
        follow=True,
    )

    assert response.status_code == 200
    pr.refresh_from_db()
    assert captured["repository"] == RepositoryRef(owner="arthexis", name="arthexis")
    assert captured["number"] == 107
    assert captured["merge_method"] == "squash"
    assert pr.state == "closed"
    assert pr.merged_at is not None
