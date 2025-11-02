from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

pytest.importorskip("graphene_django")
pytest.importorskip("graphene")

QUERY = {"query": "{ energyExportStatus { ready message } }"}


@pytest.fixture()
def graphql_url() -> str:
    return reverse("graphql")


@pytest.fixture()
def export_user(django_user_model):
    user = django_user_model.objects.create_user("exporter", password="password")
    perms = Permission.objects.filter(codename__in=["view_transaction", "view_metervalue"])
    user.user_permissions.add(*perms)
    return user


@pytest.mark.django_db()
def test_session_requests_require_csrf(client, export_user, graphql_url):
    client.force_login(export_user)
    response = client.post(
        graphql_url,
        data=json.dumps(QUERY),
        content_type="application/json",
    )
    assert response.status_code == 403


@pytest.mark.django_db()
def test_token_header_bypasses_csrf(client, export_user, graphql_url):
    client.force_login(export_user)
    response = client.post(
        graphql_url,
        data=json.dumps(QUERY),
        content_type="application/json",
        HTTP_AUTHORIZATION="Token example-token",
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["energyExportStatus"]["ready"] is True


@pytest.mark.django_db()
def test_requires_authentication(client, graphql_url):
    response = client.post(
        graphql_url,
        data=json.dumps(QUERY),
        content_type="application/json",
        HTTP_AUTHORIZATION="Token anonymous",
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["errors"][0]["message"] == "Authentication required."


@pytest.mark.django_db()
def test_requires_permissions(client, django_user_model, graphql_url):
    user = django_user_model.objects.create_user("no-perms", password="password")
    client.force_login(user)
    response = client.post(
        graphql_url,
        data=json.dumps(QUERY),
        content_type="application/json",
        HTTP_AUTHORIZATION="Token session",
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["errors"][0]["message"] == "You do not have permission to access energy export data."
