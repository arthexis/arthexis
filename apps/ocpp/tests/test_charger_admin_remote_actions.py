"""Tests for remote action registration on ChargerAdmin."""

import pytest
from django.contrib.admin.sites import AdminSite

from apps.ocpp.admin import ChargerAdmin
from apps.ocpp.models import Charger

pytestmark = pytest.mark.django_db


def test_charger_admin_remote_actions_still_registered():
    """Remote/OCPP admin actions remain available after refactor."""
    admin = ChargerAdmin(Charger, AdminSite())

    assert "change_availability_operative" in admin.actions
    assert "clear_authorization_cache" in admin.actions
    assert "reset_chargers" in admin.actions
