import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse


pytestmark = [pytest.mark.django_db]


def test_login_view_prefills_username_from_query_param(client):
    response = client.get(reverse("pages:login"), {"username": "access-user"})

    assert response.status_code == 200
    assert 'name="username"' in response.content.decode()
    assert 'value="access-user"' in response.content.decode()


def test_login_view_check_mode_prefers_authenticated_username_over_query_prefill(client):
    user = get_user_model().objects.create_user(
        username="existing-user",
        email="existing-user@example.com",
        password="secret",
    )
    client.force_login(user)

    response = client.get(reverse("pages:login"), {"check": "1", "username": "spoofed-user"})

    assert response.status_code == 200
    body = response.content.decode()
    assert 'value="existing-user"' in body
    assert 'readonly aria-readonly="true"' in body
