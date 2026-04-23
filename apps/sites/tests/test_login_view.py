import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.sites.session_keys import REGISTRATION_USERNAME_PREFILL_SESSION_KEY

pytestmark = [pytest.mark.django_db]

def test_login_view_does_not_consume_registration_session_prefill_on_post(client):
    session = client.session
    session[REGISTRATION_USERNAME_PREFILL_SESSION_KEY] = "session-registered-user"
    session.save()

    client.post(reverse("pages:login"), {"username": "", "password": ""})

    session = client.session
    assert session.get(REGISTRATION_USERNAME_PREFILL_SESSION_KEY) == "session-registered-user"

