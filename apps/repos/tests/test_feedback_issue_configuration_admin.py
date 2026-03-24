"""Regression tests for the feedback issue configuration admin tool action."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.utils import OperationalError
from django.urls import reverse
from django.utils import timezone

from apps.features.models import Feature
from apps.repos.models.issues import RepositoryIssue
from apps.repos.models.repositories import GitHubRepository


def _create_admin_client(client):
    user = get_user_model().objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin123",
    )
    client.force_login(user)
    return client


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

    response = client.get(
        reverse("admin:repos_repositoryissue_configure", args=[issue.pk])
    )

    assert response.status_code == 403


def test_repository_issue_configure_view_renders_when_feature_table_is_unavailable(
    client, db, monkeypatch
):
    admin_client = _create_admin_client(client)
    repository = GitHubRepository.objects.create(owner="arthexis", name="arthexis")
    issue = RepositoryIssue.objects.create(
        repository=repository,
        number=105,
        title="Migration safety",
        state="open",
        created_at=timezone.now(),
        updated_at=timezone.now(),
    )

    def _raise_operational_error(*args, **kwargs):
        raise OperationalError("no such table: features_feature")

    monkeypatch.setattr(Feature.objects, "filter", _raise_operational_error)
    monkeypatch.setattr(Feature.objects, "get_or_create", _raise_operational_error)

    response = admin_client.get(
        reverse("admin:repos_repositoryissue_configure", args=[issue.pk])
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "Disabled or missing" in content
    assert "Automatic GitHub exception reporting" in content
