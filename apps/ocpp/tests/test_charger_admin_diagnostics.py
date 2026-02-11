"""Tests for diagnostics-specific charger admin behavior."""

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.ocpp.admin import ChargerAdmin
from apps.ocpp.models import Charger

pytestmark = pytest.mark.django_db


def test_charger_admin_registers_diagnostics_actions():
    """Diagnostics actions remain registered after module split."""
    admin = ChargerAdmin(Charger, AdminSite())

    assert "setup_cp_diagnostics" in admin.actions
    assert "request_cp_diagnostics" in admin.actions
    assert "get_diagnostics" in admin.actions


def test_charger_admin_view_in_site_url(client):
    """Compatibility URL remains available through get_urls wiring."""
    user = get_user_model().objects.create_superuser(
        username="admin", password="pass", email="admin@example.com"
    )
    client.force_login(user)

    response = client.get(reverse("admin:ocpp_charger_view_charge_point_dashboard"))

    assert response.status_code == 302
