"""Admin integration coverage for GitHub repository setup-token shortcuts."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.repos.models.github_tokens import GitHubToken
from apps.repos.models.repositories import GitHubRepository


def _login_superuser(client, username="repos-admin"):
    user = get_user_model().objects.create_superuser(
        username=username,
        email=f"{username}@example.com",
        password="admin123",
    )
    client.force_login(user)
    return user


def test_github_repository_changelist_exposes_setup_token_tool(client, db):
    user = _login_superuser(client, username="setup-tool-admin")
    GitHubRepository.objects.create(owner="arthexis", name="arthexis")

    response = client.get(reverse("admin:repos_githubrepository_changelist"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Setup Token" in content
    assert reverse("admin:repos_githubrepository_setup_token") in content
    assert user.username in content


def test_github_repository_setup_token_redirects_to_existing_user_token(client, db):
    user = _login_superuser(client, username="existing-token-admin")
    token = GitHubToken.objects.create(user=user, token="ghp_existing", label="Existing")

    response = client.get(reverse("admin:repos_githubrepository_setup_token"))

    assert response.status_code == 302
    assert response["Location"] == reverse("admin:repos_githubtoken_change", args=[token.pk])


def test_github_repository_setup_token_redirects_to_add_when_missing(client, db):
    _login_superuser(client, username="new-token-admin")

    response = client.get(reverse("admin:repos_githubrepository_setup_token"))

    assert response.status_code == 302
    assert response["Location"] == reverse("admin:repos_githubtoken_add")


def test_admin_index_lists_setup_token_dashboard_action(client, db):
    _login_superuser(client, username="dashboard-action-admin")
    GitHubRepository.objects.create(owner="arthexis", name="arthexis")

    response = client.get(reverse("admin:index"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Setup Token" in content
    assert reverse("admin:repos_githubrepository_setup_token") in content
