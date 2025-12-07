import pytest
from django.test import Client
from django.urls import reverse


@pytest.fixture
def csrf_client():
    return Client(enforce_csrf_checks=True)


@pytest.mark.django_db
def test_request_invite_sets_csrf_cookie(csrf_client):
    response = csrf_client.get(reverse("pages:request-invite"))

    assert response.status_code == 200
    assert "csrftoken" in response.cookies


@pytest.mark.django_db
def test_request_invite_rejects_missing_csrf(csrf_client):
    response = csrf_client.post(
        reverse("pages:request-invite"),
        {"email": "user@example.com"},
    )

    assert response.status_code == 403
