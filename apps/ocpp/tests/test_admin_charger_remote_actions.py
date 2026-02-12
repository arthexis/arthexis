import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test.client import RequestFactory

from apps.ocpp.admin.charger import ChargerAdmin
from apps.ocpp.models import Charger


pytestmark = pytest.mark.django_db


def test_prepare_remote_credentials_reports_when_local_node_missing():
    """Remote actions should report a clear error when no local node exists."""
    user = get_user_model().objects.create_superuser(
        username="remote-admin",
        password="pass",
        email="remote-admin@example.com",
    )
    request = RequestFactory().get("/")
    request.user = user
    request.session = {}
    setattr(request, "_messages", FallbackStorage(request))

    admin = ChargerAdmin(Charger, AdminSite())

    local, private_key = admin._prepare_remote_credentials(request)

    assert local is None
    assert private_key is None
    messages = [m.message for m in list(request._messages)]
    assert any("Local node is not registered" in msg for msg in messages)


def test_apply_remote_updates_ignores_unexpected_fields():
    """Remote updates should only persist explicit allow-listed fields."""
    charger = Charger.objects.create(charger_id="CP-REMOTE-1", require_rfid=False)
    original_allow_remote = charger.allow_remote
    original_ws_auth_user = charger.ws_auth_user
    admin = ChargerAdmin(Charger, AdminSite())

    admin._apply_remote_updates(
        charger,
        {
            "require_rfid": True,
            "allow_remote": False,
            "ws_auth_user": "evil-user",
        },
    )

    charger.refresh_from_db()

    assert charger.require_rfid is True
    assert charger.allow_remote == original_allow_remote
    assert charger.ws_auth_user == original_ws_auth_user
