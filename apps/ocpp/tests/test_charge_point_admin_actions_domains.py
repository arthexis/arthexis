"""Regression tests for split charger admin action domain mixins."""

import uuid
from unittest.mock import PropertyMock, patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test.client import RequestFactory

from apps.ocpp.admin import ChargerAdmin
from apps.ocpp.models import Charger, Simulator


pytestmark = pytest.mark.django_db


def _admin_request():
    """Build an authenticated admin request with message storage."""
    user = get_user_model().objects.create_superuser(
        username=f"admin-actions-{uuid.uuid4().hex[:8]}",
        password="pass",
        email="admin-actions@example.com",
    )
    request = RequestFactory().post("/")
    request.user = user
    request.session = {}
    setattr(request, "_messages", FallbackStorage(request))
    return request


def _messages(request):
    """Return collected django admin message strings."""
    return [message.message for message in list(request._messages)]


def test_diagnostics_action_reports_remote_permission_error():
    """Regression: diagnostics action keeps remote permission check and message."""
    admin = ChargerAdmin(Charger, AdminSite())
    request = _admin_request()
    charger = Charger.objects.create(charger_id="CP-REMOTE-DIAG", allow_remote=False)

    with patch.object(Charger, "is_local", new_callable=PropertyMock, return_value=False):
        admin.request_cp_diagnostics(request, Charger.objects.filter(pk=charger.pk))

    assert any("remote administration is disabled" in message for message in _messages(request))


def test_authorization_toggle_updates_local_state():
    """Regression: toggle_rfid_authentication still flips local charger state."""
    admin = ChargerAdmin(Charger, AdminSite())
    request = _admin_request()
    charger = Charger.objects.create(charger_id="CP-LOCAL-RFID", require_rfid=False)

    admin.toggle_rfid_authentication(request, Charger.objects.filter(pk=charger.pk))

    charger.refresh_from_db()
    assert charger.require_rfid is True
    assert any("Updated RFID authentication" in message for message in _messages(request))


def test_availability_change_updates_local_request_state():
    """Regression: change availability writes request tracking fields."""
    admin = ChargerAdmin(Charger, AdminSite())
    request = _admin_request()
    charger = Charger.objects.create(charger_id="CP-LOCAL-AV")

    with patch.object(admin, "_send_local_ocpp_call", return_value=True):
        admin.change_availability_operative(request, Charger.objects.filter(pk=charger.pk))

    charger.refresh_from_db()
    assert charger.availability_requested_state == "Operative"
    assert any("Sent ChangeAvailability" in message for message in _messages(request))


def test_remote_control_unlock_requires_connector_id():
    """Regression: unlock action still rejects missing/aggregate connector ids."""
    admin = ChargerAdmin(Charger, AdminSite())
    request = _admin_request()
    charger = Charger.objects.create(charger_id="CP-UNLOCK")

    admin.unlock_connector(request, Charger.objects.filter(pk=charger.pk))

    assert any("connector id is required" in message for message in _messages(request))


def test_simulator_action_creates_and_redirects():
    """Regression: simulator action creates simulator and redirects to change view."""
    admin = ChargerAdmin(Charger, AdminSite())
    request = _admin_request()
    charger = Charger.objects.create(charger_id="CP-SIM")

    response = admin.create_simulator_for_cp(request, Charger.objects.filter(pk=charger.pk))

    simulator = Simulator.objects.get(serial_number="CP-SIM")
    assert response.status_code == 302
    assert str(simulator.pk) in response.url
    assert any("Created 1 simulator" in message for message in _messages(request))
