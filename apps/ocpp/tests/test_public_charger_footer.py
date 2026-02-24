"""Regression tests for hiding the default footer on charger pages."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.ocpp.models import Charger


pytestmark = [pytest.mark.django_db, pytest.mark.regression]


def _login_user(client):
    """Create and log in a deterministic test user."""
    user = get_user_model().objects.create_user(
        username="footer-user", password="pass12345"
    )
    client.force_login(user)
    return user


def test_charger_status_hides_default_footer(client):
    """The charger status page should not render the default footer placeholder."""
    _login_user(client)
    charger = Charger.objects.create(charger_id="FOOTER-CP-1")

    response = client.get(reverse("ocpp:charger-status", args=[charger.charger_id]))

    assert response.status_code == 200
    assert b"footer-placeholder" not in response.content


def test_charger_session_search_hides_default_footer(client):
    """The session search page should not render the default footer placeholder."""
    _login_user(client)
    charger = Charger.objects.create(charger_id="FOOTER-CP-2")

    response = client.get(
        reverse("ocpp:charger-session-search", args=[charger.charger_id])
    )

    assert response.status_code == 200
    assert b"footer-placeholder" not in response.content


def test_charger_log_page_hides_default_footer(client):
    """The charger log page should not render the default footer placeholder."""
    _login_user(client)
    charger = Charger.objects.create(charger_id="FOOTER-CP-3")

    response = client.get(reverse("ocpp:charger-log", args=[charger.charger_id]))

    assert response.status_code == 200
    assert b"footer-placeholder" not in response.content
