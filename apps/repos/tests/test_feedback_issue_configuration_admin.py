"""Regression tests for the feedback issue configuration admin tool action."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.features.models import Feature
from apps.release.models import Package
from apps.repos.models.issues import RepositoryIssue
from apps.repos.models.repositories import GitHubRepository


def _create_admin_client(client):
    """Return a logged-in admin client with deterministic credentials."""

    user = get_user_model().objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin123",
    )
    client.force_login(user)
    return client


def test_repository_issue_configure_view_renders_validation_form(client, db):
    """Change view should expose a Configure destination for repository issues."""

    admin_client = _create_admin_client(client)
    repository = GitHubRepository.objects.create(owner="arthexis", name="arthexis")
    issue = RepositoryIssue.objects.create(
        repository=repository,
        number=101,
        title="Example",
        state="open",
        created_at=timezone.now(),
        updated_at=timezone.now(),
    )

    response = admin_client.get(
        reverse("admin:repos_repositoryissue_configure", args=[issue.pk])
    )

    assert response.status_code == 200
    assert "Configure feedback issue automation" in response.content.decode()


def test_repository_issue_configure_view_updates_feature_and_repository(client, db):
    """Configure view should persist editable fields and re-run validation."""

    admin_client = _create_admin_client(client)
    feature = Feature.objects.create(
        slug="feedback-ingestion",
        display="Feedback Ingestion",
        is_enabled=False,
    )
    package = Package.objects.create(
        name="suite",
        repository_url="https://github.com/arthexis/old",
        is_active=True,
    )
    repository = GitHubRepository.objects.create(owner="arthexis", name="arthexis")
    issue = RepositoryIssue.objects.create(
        repository=repository,
        number=102,
        title="Another",
        state="open",
        created_at=timezone.now(),
        updated_at=timezone.now(),
    )

    response = admin_client.post(
        reverse("admin:repos_repositoryissue_configure", args=[issue.pk]),
        data={
            "feedback_ingestion_enabled": "on",
            "active_repository_url": "https://github.com/arthexis/new-repo",
        },
        follow=True,
    )

    feature.refresh_from_db()
    package.refresh_from_db()

    assert response.status_code == 200
    assert feature.is_enabled is True
    assert package.repository_url == "https://github.com/arthexis/new-repo"
