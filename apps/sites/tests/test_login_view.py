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
    assert (
        session.get(REGISTRATION_USERNAME_PREFILL_SESSION_KEY)
        == "session-registered-user"
    )


def test_login_view_hides_navigation_and_funding_banner(client, monkeypatch):
    monkeypatch.setattr(
        "apps.sites.context_processors._is_github_issue_open",
        lambda *_args, **_kwargs: True,
    )

    response = client.get(reverse("pages:login"), HTTP_HOST="arthexis.com")
    html = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "navbar navbar-expand-lg" not in html
    assert "View funding issue" not in html
