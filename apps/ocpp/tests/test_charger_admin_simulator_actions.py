"""Tests for simulator action registration on ChargerAdmin."""

import pytest
from django.contrib.admin.sites import AdminSite

from apps.ocpp.admin import ChargerAdmin
from apps.ocpp.models import Charger

pytestmark = pytest.mark.django_db


def test_charger_admin_simulator_action_still_registered():
    """Simulator creation action remains registered after module split."""
    admin = ChargerAdmin(Charger, AdminSite())

    assert "create_simulator_for_cp" in admin.actions
