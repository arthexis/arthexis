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



def test_apply_remote_updates_uses_allow_list_only():
    """Remote updates should ignore non-allowlisted charger fields."""
    admin = ChargerAdmin(Charger, AdminSite())
    charger = Charger.objects.create(charger_id="CP-REMOTE-UPDATES", allow_remote=True, require_rfid=False)

    admin._apply_remote_updates(charger, {"allow_remote": False, "require_rfid": True})

    charger.refresh_from_db()
    assert charger.allow_remote is True
    assert charger.require_rfid is True


def test_remote_toggle_counts_use_requested_value():
    """Remote toggle summary should use intended toggle value when updates are empty."""
    admin = ChargerAdmin(Charger, AdminSite())
    request = _admin_request()
    charger = Charger.objects.create(charger_id="CP-REMOTE-RFID", require_rfid=False, allow_remote=True)

    with (
        patch.object(admin, "_iter_chargers", return_value=iter([(charger, False, object(), object())])),
        patch.object(admin, "_call_remote_action", return_value=(True, {})),
        patch.object(admin, "_apply_remote_updates"),
    ):
        admin.toggle_rfid_authentication(request, Charger.objects.filter(pk=charger.pk))

    assert any("enabled for 1 charger(s)" in message for message in _messages(request))


def test_non_dict_remote_response_is_rejected():
    """Remote responses must be dict-like JSON payloads."""
    admin = ChargerAdmin(Charger, AdminSite())
    request = _admin_request()
    charger = Charger.objects.create(charger_id="CP-REMOTE-JSON", allow_remote=True)

    class _Origin:
        port = 443

        @staticmethod
        def get_remote_host_candidates():
            return ["example.com"]

        @staticmethod
        def iter_remote_urls(_path):
            yield "https://example.com/nodes/network/chargers/action/"

    class _LocalNode:
        uuid = "11111111-1111-1111-1111-111111111111"
        mac_address = "00:11:22:33:44:55"
        public_key = "pk"

    class _PrivateKey:
        @staticmethod
        def sign(*_args, **_kwargs):
            return b"sig"

    class _Response:
        status_code = 200
        text = "[]"

        @staticmethod
        def json():
            return []

    with (
        patch.object(Charger, "node_origin", new_callable=PropertyMock, return_value=_Origin()),
        patch("apps.ocpp.admin.charge_point.actions.services.requests.post", return_value=_Response()),
    ):
        ok, updates = admin._call_remote_action(request, _LocalNode(), _PrivateKey(), charger, "get-configuration")

    assert ok is False
    assert updates == {}
    assert any("[]" in message or "Remote node rejected" in message for message in _messages(request))


def test_purge_data_reports_partial_failures():
    """Purge should continue after per-charger failures and report the result."""
    admin = ChargerAdmin(Charger, AdminSite())
    request = _admin_request()
    charger_a = Charger.objects.create(charger_id="CP-PURGE-OK")
    charger_b = Charger.objects.create(charger_id="CP-PURGE-FAIL")

    def _fake_purge(self):
        if self.pk == charger_b.pk:
            raise RuntimeError("boom")

    with patch.object(Charger, "purge", _fake_purge):
        admin.purge_data(request, Charger.objects.filter(pk__in=[charger_a.pk, charger_b.pk]).order_by("pk"))

    msgs = _messages(request)
    assert any("Purged selected charge point data" in message for message in msgs)
    assert any("Failed to purge 1 charger" in message for message in msgs)
