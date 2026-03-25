"""Admin integration coverage for GitHub repository setup-token shortcuts."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.urls import reverse

from apps.repos.models.github_tokens import GitHubToken
from apps.repos.models.repositories import GitHubRepository


def _login_admin_user(client, username="repos-admin", *, is_superuser=True, permission_codenames=()):
    user_model = get_user_model()
    if is_superuser:
        user = user_model.objects.create_superuser(
            username=username,
            email=f"{username}@example.com",
            password="admin123",
        )
    else:
        user = user_model.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="admin123",
            is_staff=True,
            is_superuser=False,
        )
    if permission_codenames:
        permissions = Permission.objects.filter(codename__in=permission_codenames)
        user.user_permissions.set(permissions)
    client.force_login(user)
    return user


def test_github_repository_changelist_exposes_setup_token_tool(client, db):
    user = _login_admin_user(client, username="setup-tool-admin")
    GitHubRepository.objects.create(owner="arthexis", name="arthexis")

    response = client.get(reverse("admin:repos_githubrepository_changelist"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Setup Token" in content
    assert reverse("admin:repos_githubrepository_setup_token") in content
    assert user.username in content


def test_github_repository_setup_token_redirects_to_existing_user_token(client, db):
    user = _login_admin_user(client, username="existing-token-admin")
    token = GitHubToken.objects.create(user=user, token="ghp_existing", label="Existing")

    response = client.get(reverse("admin:repos_githubrepository_setup_token"))

    assert response.status_code == 302
    assert response["Location"] == reverse("admin:repos_githubtoken_change", args=[token.pk])


def test_github_repository_setup_token_redirects_to_add_when_missing(client, db):
    _login_admin_user(client, username="new-token-admin")

    response = client.get(reverse("admin:repos_githubrepository_setup_token"))

    assert response.status_code == 302
    assert response["Location"] == reverse("admin:repos_githubtoken_add")


def test_admin_index_lists_setup_token_dashboard_action(client, db):
    _login_admin_user(client, username="dashboard-action-admin")
    GitHubRepository.objects.create(owner="arthexis", name="arthexis")

    response = client.get(reverse("admin:index"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Setup Token" in content
    assert reverse("admin:repos_githubrepository_setup_token") in content


def test_dashboard_action_is_hidden_without_token_permissions(client, db):
    _login_admin_user(
        client,
        username="limited-admin",
        is_superuser=False,
        permission_codenames=["view_githubrepository"],
    )
    GitHubRepository.objects.create(owner="arthexis", name="arthexis")

    dashboard = client.get(reverse("admin:index"))

    dashboard_content = dashboard.content.decode()
    setup_url = reverse("admin:repos_githubrepository_setup_token")
    assert setup_url not in dashboard_content


def test_setup_token_redirects_to_changelist_without_token_permissions(client, db):
    user = _login_admin_user(
        client,
        username="no-token-perms-admin",
        is_superuser=False,
        permission_codenames=["view_githubrepository"],
    )
    GitHubToken.objects.create(user=user, token="ghp_existing", label="Existing")

    response = client.get(reverse("admin:repos_githubrepository_setup_token"), follow=True)

    assert response.status_code == 200
    assert response.request["PATH_INFO"] == reverse("admin:repos_githubrepository_changelist")
    message_texts = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("permission" in text.lower() for text in message_texts)
