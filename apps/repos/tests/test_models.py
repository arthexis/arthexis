from __future__ import annotations

import pytest
from django.utils import timezone

from apps.repos.models.issues import RepositoryIssue, RepositoryPullRequest
from apps.repos.models.repositories import GitHubRepository


@pytest.mark.django_db
def test_github_repository_persistence_fields():
    repo = GitHubRepository.objects.create(
        owner="octocat",
        name="hello-world",
        description="Example description",
        is_private=True,
        default_branch="main",
    )

    assert repo.slug == "octocat/hello-world"
    assert repo.natural_key() == ("octocat", "hello-world")
    assert repo.default_branch == "main"


@pytest.mark.django_db
def test_repository_issue_fields():
    repo = GitHubRepository.objects.create(owner="octocat", name="hello-world")
    now = timezone.now()

    issue = RepositoryIssue.objects.create(
        repository=repo,
        number=42,
        title="Test issue",
        state="open",
        html_url="https://example.com/issue",
        api_url="https://api.example.com/issue",
        author="octocat",
        created_at=now,
        updated_at=now,
    )

    assert issue.repository == repo
    assert str(issue).startswith("#42")
    assert issue.state == "open"


@pytest.mark.django_db
def test_repository_pull_request_fields():
    repo = GitHubRepository.objects.create(owner="octocat", name="hello-world")
    now = timezone.now()

    pr = RepositoryPullRequest.objects.create(
        repository=repo,
        number=7,
        title="Add feature",
        state="open",
        html_url="https://example.com/pr",
        api_url="https://api.example.com/pr",
        author="octocat",
        created_at=now,
        updated_at=now,
        merged_at=None,
        source_branch="feature",
        target_branch="main",
        is_draft=False,
    )

    assert pr.repository == repo
    assert str(pr).startswith("PR #7")
    assert pr.source_branch == "feature"
