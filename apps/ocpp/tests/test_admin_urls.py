import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import ValidationError
from django.test.client import RequestFactory

from apps.ocpp.admin import ChargerAdmin
from apps.ocpp.models import Charger, Variable, MonitoringRule, MonitoringReport

pytestmark = pytest.mark.django_db


def test_charger_admin_changelist_accessible(client):
    User = get_user_model()
    user = User.objects.create_superuser(username="admin", password="pass", email="admin@example.com")
    client.force_login(user)

    url = reverse("admin:ocpp_charger_changelist")
    response = client.get(url)

    assert response.status_code == 200
    assert b"Charge Point" in response.content


def test_charger_admin_changelist_populates_quick_stats(client):
    User = get_user_model()
    user = User.objects.create_superuser(username="admin", password="pass", email="admin@example.com")
    client.force_login(user)

    Charger.objects.create(charger_id="CP-ADMIN")
    Charger.objects.create(charger_id="CP-ADMIN", connector_id=1)

    response = client.get(reverse("admin:ocpp_charger_changelist"))

    assert response.status_code == 200
    context = response.context[-1]
    assert "charger_quick_stats" in context
    stats = context["charger_quick_stats"]
    assert stats["total_kw"] == 0.0
    assert stats["today_kw"] == 0.0
    assert stats["estimated_cost"] is None
    assert stats["availability_percentage"] is None


def test_charger_admin_reports_validation_error(db):
    User = get_user_model()
    admin_user = User.objects.create_superuser(
        username="admin", password="pass", email="admin@example.com"
    )
    request = RequestFactory().get("/")
    request.user = admin_user
    request.session = {}
    messages = FallbackStorage(request)
    setattr(request, "_messages", messages)

    admin_site = AdminSite()
    admin = ChargerAdmin(Charger, admin_site)
    charger = Charger.objects.create(charger_id="TEST-CP")

    admin._report_simulator_error(
        request,
        charger,
        ValidationError({"charger_id": ["Invalid"]}),
    )

    stored_messages = [message.message for message in list(request._messages)]
    assert any("Unable to create simulator" in message for message in stored_messages)


def test_monitoring_admin_views_accessible(client):
    User = get_user_model()
    user = User.objects.create_superuser(username="admin", password="pass", email="admin@example.com")
    client.force_login(user)

    charger = Charger.objects.create(charger_id="CP-MON")
    variable = Variable.objects.create(
        charger=charger,
        component_name="EVSE",
        variable_name="Voltage",
        attribute_type="Actual",
        value="230",
    )
    MonitoringRule.objects.create(
        charger=charger,
        variable=variable,
        monitoring_id=10,
        monitor_type="UpperThreshold",
        threshold="240",
        severity=5,
    )
    MonitoringReport.objects.create(charger=charger, request_id=99, seq_no=1)

    assert client.get(reverse("admin:ocpp_variable_changelist")).status_code == 200
    assert client.get(reverse("admin:ocpp_monitoringrule_changelist")).status_code == 200
    assert client.get(reverse("admin:ocpp_monitoringreport_changelist")).status_code == 200
