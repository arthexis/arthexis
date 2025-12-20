"""Tests for the admin bulk reference creation endpoint."""

import json
import uuid

import pytest
from django.urls import reverse

from apps.links.models import Reference


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


@pytest.mark.django_db
def test_bulk_create_get_returns_method_not_allowed_for_staff(admin_client):
    url = reverse("admin:links_reference_bulk")

    response = admin_client.get(url)

    assert response.status_code == 405


@pytest.mark.django_db
def test_bulk_create_creates_references_for_staff(admin_client, admin_user):
    url = reverse("admin:links_reference_bulk")
    transaction_uuid = str(uuid.uuid4())
    payload = {
        "transaction_uuid": transaction_uuid,
        "references": [
            {"alt_text": "Docs", "value": "https://example.com/docs"},
            {"alt_text": "Blog", "value": "https://example.com/blog"},
        ],
    }

    response = admin_client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["transaction_uuid"] == transaction_uuid
    assert body["ids"]

    created_refs = list(Reference.objects.filter(id__in=body["ids"]))
    assert len(created_refs) == 2
    assert {ref.transaction_uuid for ref in created_refs} == {
        uuid.UUID(transaction_uuid)
    }
    assert {ref.author for ref in created_refs} == {admin_user}
