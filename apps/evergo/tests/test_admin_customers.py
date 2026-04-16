"""Regression tests for Evergo customer admin views."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.evergo.models import EvergoCustomer, EvergoUser


@pytest.mark.django_db
def test_evergo_customer_changelist_renders_with_json_brand_payload(client):
    """Customer changelist should render when brand comes from JSON payload only."""
    User = get_user_model()
    admin_user = User.objects.create_superuser(
        username="evergo-admin",
        email="evergo-admin@example.com",
        password="top-secret",  # noqa: S106
    )
    profile = EvergoUser.objects.create(
        user=admin_user,
        evergo_email="contractor@example.com",
        evergo_password="top-secret",  # noqa: S106
    )
    EvergoCustomer.objects.create(
        user=profile,
        name="Customer With Brand",
        raw_payload={"orden_instalacion": {"marca_cargador": "ABB"}},
    )

    client.force_login(admin_user)
    response = client.get(reverse("admin:evergo_evergocustomer_changelist"))

    assert response.status_code == 200
    assert b"Customer With Brand" in response.content
