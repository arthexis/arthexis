"""Regression tests for feedback issue configuration admin permissions."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.repos.models.issues import RepositoryIssue
from apps.repos.models.repositories import GitHubRepository


def test_repository_issue_configure_view_returns_403_without_change_permission(client, db):
    user = get_user_model().objects.create_user(
        username="staff-no-change",
        email="staff@example.com",
        password="password123",
        is_staff=True,
    )
    client.force_login(user)

    repository = GitHubRepository.objects.create(owner="arthexis", name="arthexis")
    issue = RepositoryIssue.objects.create(
        repository=repository,
        number=104,
        title="Permission check",
        state="open",
        created_at=timezone.now(),
        updated_at=timezone.now(),
    )

    response = client.get(reverse("admin:repos_repositoryissue_configure", args=[issue.pk]))

    assert response.status_code == 403
