from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
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
    def fail_issue_lookup(*_args, **_kwargs):
        raise AssertionError("login render must not check GitHub funding issue state")

    monkeypatch.setattr(
        "apps.sites.context_processors._is_github_issue_open",
        fail_issue_lookup,
    )

    response = client.get(reverse("pages:login"), HTTP_HOST="arthexis.com")
    html = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "navbar navbar-expand-lg" not in html
    assert "View funding issue" not in html


def test_login_view_ignores_unsafe_next(client):
    response = client.get(
        reverse("pages:login"),
        {"next": "https://attacker.example/admin/"},
    )

    assert response.status_code == 200
    assert response.context["next"] == "/"
    assert "sessionid" not in response.cookies
    assert Session.objects.count() == 0


@pytest.mark.parametrize("target", ["https://attacker.example/", "//attacker.example/"])
def test_logout_view_rejects_external_redirect_targets(client, target):
    response = client.get(reverse("pages:logout"), {"next": target})

    assert response.status_code == 302
    assert response.url == reverse("pages:login")


def test_login_view_passes_safe_next_to_rfid_login_url(client, monkeypatch):
    node = SimpleNamespace(
        role=None,
        has_feature=lambda slug: slug in {"rfid", "rfid-scanner"},
    )
    monkeypatch.setattr("apps.sites.views.management.Node.get_local", lambda: node)
    monkeypatch.setattr(
        "apps.sites.views.management.ensure_feature_enabled",
        lambda *args, **kwargs: None,
    )

    response = client.get(
        reverse("pages:login"),
        {
            "next": "/release-checklist/",
        },
    )

    assert response.status_code == 200
    parsed = urlparse(response.context["rfid_login_url"])
    assert parsed.path == reverse("pages:rfid-login")
    assert parse_qs(parsed.query) == {"next": ["/release-checklist/"]}
    assert "sessionid" not in response.cookies
    assert Session.objects.count() == 0


def test_login_view_redirects_to_safe_next_after_successful_login(client):
    user = get_user_model().objects.create_user(
        username="login-user",
        password="login-password",
    )

    response = client.post(
        reverse("pages:login"),
        {
            "username": user.username,
            "password": "login-password",
            "next": "/release-checklist/",
        },
    )

    assert response.status_code == 302
    assert response.url == "/release-checklist/"
