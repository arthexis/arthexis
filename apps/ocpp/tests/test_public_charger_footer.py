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


@pytest.mark.parametrize(
    ("url_name", "charger_id_suffix"),
    [
        ("ocpp:charger-status", "1"),
        ("ocpp:charger-session-search", "2"),
        ("ocpp:charger-log", "3"),
    ],
)
def test_public_charger_pages_hide_default_footer(client, url_name, charger_id_suffix):
    """Public charger pages should not render the default footer placeholder."""
    _login_user(client)
    charger = Charger.objects.create(charger_id=f"FOOTER-CP-{charger_id_suffix}")

    response = client.get(reverse(url_name, args=[charger.charger_id]))

    assert response.status_code == 200
    assert b"footer-placeholder" not in response.content
