import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import ValidationError
from django.test.client import RequestFactory

from apps.ocpp.admin.charger import ChargerAdmin
from apps.ocpp.models import Charger


pytestmark = pytest.mark.django_db


def test_report_simulator_error_still_emits_admin_message():
    """Simulator failures should still be surfaced through admin messages."""
    user = get_user_model().objects.create_superuser(
        username="admin-sim",
        password="pass",
        email="admin-sim@example.com",
    )
    request = RequestFactory().get("/")
    request.user = user
    request.session = {}
    setattr(request, "_messages", FallbackStorage(request))

    admin = ChargerAdmin(Charger, AdminSite())
    charger = Charger.objects.create(charger_id="SIM-CP")

    admin._report_simulator_error(
        request,
        charger,
        ValidationError({"charger_id": ["Invalid"]}),
    )

    stored_messages = [message.message for message in list(request._messages)]
    assert any("Unable to create simulator" in message for message in stored_messages)
