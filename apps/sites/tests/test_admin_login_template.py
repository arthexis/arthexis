"""Regression tests for admin login template customizations."""

import pytest
from django.urls import reverse

from apps.features.models import Feature


@pytest.mark.django_db
@pytest.mark.integration
def test_admin_login_hides_branding_badges_and_feedback_button(client):
    """Login page should hide environment badges and feedback toggle."""

    response = client.get(reverse("admin:login"))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="site-badges"' not in content
    assert 'id="server-clock"' not in content
    assert 'id="user-story-toggle"' not in content


@pytest.mark.django_db
@pytest.mark.integration
def test_admin_index_keeps_branding_badges_and_feedback_button(admin_client):
    """Admin index should continue rendering badges and feedback toggle."""

    response = admin_client.get(reverse("admin:index"))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="site-badges"' in content
    assert "SITE:" in content
    assert "NODE:" in content
    assert "ROLE:" in content
    assert "CAM:" not in content
    assert 'id="server-clock"' in content
    assert 'id="user-story-toggle"' in content


@pytest.mark.django_db
@pytest.mark.regression
def test_admin_index_hides_feedback_button_when_feedback_ingestion_disabled(
    admin_client,
):
    """Regression: admin index should hide feedback UI when ingestion feature is disabled."""

    Feature.objects.update_or_create(
        slug="feedback-ingestion",
        defaults={"display": "Feedback Ingestion", "is_enabled": False},
    )

    response = admin_client.get(reverse("admin:index"))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="user-story-toggle"' not in content
