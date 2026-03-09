"""Admin regression tests for repository models."""

import pytest

from django.contrib.auth import get_user_model
from django.urls import reverse


@pytest.mark.django_db
def test_github_token_admin_add_page_loads(client):
    """Regression: GitHub token add page renders successfully for admin users."""

    user_model = get_user_model()
    admin_user = user_model.objects.create_superuser(
        username="repo-admin",
        email="repo-admin@example.com",
        password="admin123",
    )
    client.force_login(admin_user)

    response = client.get(reverse("admin:repos_githubtoken_add"))

    assert response.status_code == 200
