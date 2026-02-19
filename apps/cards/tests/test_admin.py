"""Regression coverage for cards admin routes used in smoke test commands."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.cards.models import CardFace


pytestmark = [pytest.mark.critical, pytest.mark.django_db]


def test_cardface_preview_route_renders_for_admin(client):
    """Regression: the standard admin preview route should be available for CardFace objects."""

    user = get_user_model().objects.create_superuser(
        username="cards-admin",
        email="cards-admin@example.com",
        password="cards-admin-pass",
    )
    client.force_login(user)
    card_face = CardFace.objects.create(name="Preview card")

    response = client.get(reverse("admin:cards_cardface_preview", args=[card_face.pk]))

    assert response.status_code == 200
    assert "Preview Card Face" in response.content.decode()
