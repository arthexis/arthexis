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
