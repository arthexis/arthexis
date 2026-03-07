"""Tests for the admin bulk reference creation endpoint."""

import json
import uuid

import pytest
from django.urls import reverse

from apps.links.models.reference import Reference


@pytest.mark.django_db
def test_bulk_create_redirects_anonymous_user(client):
    url = reverse("admin:links_reference_bulk")

    response = client.post(
        url,
        data=json.dumps({"references": []}),
        content_type="application/json",
    )

    assert response.status_code == 302
    assert reverse("admin:login") in response.url


@pytest.mark.django_db
def test_bulk_create_denies_non_staff_user(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="regular-user",
        password="password",
        is_staff=False,
    )
    client.force_login(user)
    url = reverse("admin:links_reference_bulk")

    response = client.post(
        url,
        data=json.dumps({"references": []}),
        content_type="application/json",
    )

    assert response.status_code in {302, 403}
    if response.status_code == 302:
        assert reverse("admin:login") in response.url


