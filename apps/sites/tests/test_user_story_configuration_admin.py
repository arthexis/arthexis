"""Regression tests for the user story Configure admin action."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.sites.models import UserStory


def test_user_story_configure_view_renders(client, db):
    """User story admin should expose the configure screen for issue prerequisites."""

    user = get_user_model().objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin123",
    )
    client.force_login(user)
    story = UserStory.objects.create(
        path="/feedback",
        name="Reporter",
        rating=2,
        comments="Needs fixes",
        user=user,
    )

    response = client.get(reverse("admin:sites_userstory_configure", args=[story.pk]))

    assert response.status_code == 200
    assert "Validation" in response.content.decode()
