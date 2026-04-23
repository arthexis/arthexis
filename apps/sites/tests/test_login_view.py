import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.sites.session_keys import REGISTRATION_USERNAME_PREFILL_SESSION_KEY


pytestmark = [pytest.mark.django_db]


def test_login_view_prefills_username_from_registration_session_once(client):
    session = client.session
    session[REGISTRATION_USERNAME_PREFILL_SESSION_KEY] = "session-registered-user"
    session.save()

    response = client.get(reverse("pages:login"))

    assert response.status_code == 200
    assert 'value="session-registered-user"' in response.content.decode()

    session = client.session
    assert REGISTRATION_USERNAME_PREFILL_SESSION_KEY not in session


def test_login_view_does_not_consume_registration_session_prefill_on_post(client):
    session = client.session
    session[REGISTRATION_USERNAME_PREFILL_SESSION_KEY] = "session-registered-user"
    session.save()

    client.post(reverse("pages:login"), {"username": "", "password": ""})

    session = client.session
    assert session.get(REGISTRATION_USERNAME_PREFILL_SESSION_KEY) == "session-registered-user"


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
