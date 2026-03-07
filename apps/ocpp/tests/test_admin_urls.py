import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import ValidationError
from django.test.client import RequestFactory

from apps.ocpp.admin import ChargerAdmin, ChargingStationAdmin
from apps.ocpp.models import Charger, Variable, MonitoringRule, MonitoringReport, ChargingStation

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


def test_charger_admin_changelist_shows_station_column(client):
    """Regression: include station display name separately from charge-point display name."""

    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin-station", password="pass", email="admin-station@example.com"
    )
    client.force_login(user)

    station = ChargingStation.objects.create(station_id="STAT-1", display_name="Main Station")
    Charger.objects.create(charger_id="CP-WITH-STATION", charging_station=station)

    response = client.get(reverse("admin:ocpp_charger_changelist"))

    assert response.status_code == 200
    assert b"Station" in response.content
    assert b"Main Station" in response.content


def test_charger_admin_hides_station_root_charge_points_from_list():
    """Regression: station root rows should not appear in CP admin queryset."""

    station = ChargingStation.objects.create(station_id="STAT-HIDE")
    root_cp = Charger.objects.create(
        charger_id="CP-HIDE",
        charging_station=station,
        connector_id=None,
    )
    connector_cp = Charger.objects.create(
        charger_id="CP-HIDE",
        charging_station=station,
        connector_id=1,
    )
    standalone_cp = Charger.objects.create(charger_id="CP-STANDALONE", connector_id=None)

    admin = ChargerAdmin(Charger, AdminSite())
    queryset = admin.get_queryset(RequestFactory().get("/"))

    assert root_cp not in queryset
    assert connector_cp in queryset
    assert standalone_cp in queryset


def test_charger_admin_station_managed_fields_are_readonly():
    """Regression: station-managed fields should be read-only in charge-point admin."""

    station = ChargingStation.objects.create(station_id="STAT-RO")
    charger = Charger.objects.create(charger_id="CP-RO", charging_station=station)
    admin = ChargerAdmin(Charger, AdminSite())

    readonly = admin.get_readonly_fields(request=RequestFactory().get("/"), obj=charger)

    assert "display_name" in readonly
    assert "public_display" in readonly
    assert "language" in readonly
    assert "preferred_ocpp_version" in readonly
    assert "energy_unit" in readonly
    assert "require_rfid" in readonly
    assert "location" in readonly
    assert "station_model" in readonly


def test_charging_station_admin_syncs_station_managed_cp_fields():
    """Regression: editing station admin should update linked charge-point settings."""

    station = ChargingStation.objects.create(station_id="STAT-SYNC", display_name="Station Sync")
    root_cp = Charger.objects.create(
        charger_id="CP-SYNC",
        charging_station=station,
        connector_id=None,
        public_display=False,
        require_rfid=True,
        energy_unit=Charger.EnergyUnit.W,
        preferred_ocpp_version="1.6",
    )
    child_cp = Charger.objects.create(
        charger_id="CP-SYNC",
        charging_station=station,
        connector_id=1,
        public_display=False,
        require_rfid=True,
        energy_unit=Charger.EnergyUnit.W,
    )
    admin_user = get_user_model().objects.create_superuser(
        username="admin-sync", password="pass", email="admin-sync@example.com"
    )
    request = RequestFactory().post("/")
    request.user = admin_user

    station.display_name = "Updated Station"
    form = ChargingStationAdmin.form(
        data={
            "station_id": station.station_id,
            "display_name": station.display_name,
            "public_display": True,
            "preferred_ocpp_version": "2.0.1",
            "energy_unit": Charger.EnergyUnit.KW,
            "require_rfid": False,
            "owner_users": [],
            "owner_groups": [],
        },
        instance=station,
    )
    assert form.is_valid(), form.errors

    admin_instance = ChargingStationAdmin(ChargingStation, AdminSite())
    admin_instance.save_model(request, station, form, change=True)

    root_cp.refresh_from_db()
    child_cp.refresh_from_db()
    assert root_cp.display_name == "Updated Station"
    assert child_cp.display_name == "Updated Station"
    assert root_cp.public_display is True
    assert child_cp.public_display is True
    assert root_cp.preferred_ocpp_version == "2.0.1"
    assert child_cp.preferred_ocpp_version == "2.0.1"
    assert root_cp.energy_unit == Charger.EnergyUnit.KW
    assert child_cp.energy_unit == Charger.EnergyUnit.KW
    assert root_cp.require_rfid is False
    assert child_cp.require_rfid is False


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


def test_charger_admin_actions_remain_discoverable():
    """Regression: keep key charger admin actions discoverable in dropdowns."""

    admin = ChargerAdmin(Charger, AdminSite())

    expected_actions = {
        "purge_data",
        "recheck_charger_status",
        "setup_cp_diagnostics",
        "configure_local_ftp_server",
        "request_cp_diagnostics",
        "get_diagnostics",
        "change_availability_operative",
        "change_availability_inoperative",
        "set_availability_state_operative",
        "set_availability_state_inoperative",
        "unlock_connector",
        "remote_stop_transaction",
        "reset_chargers",
        "create_simulator_for_cp",
        "setup_charger_location",
        "view_charge_point_dashboard",
        "delete_selected",
    }

    assert expected_actions.issubset(set(admin.actions))


def test_charger_admin_view_in_site_url_resolves(client):
    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin-url", password="pass", email="admin-url@example.com"
    )
    client.force_login(user)

    url = reverse("admin:ocpp_charger_view_charge_point_dashboard")
    response = client.get(url)

    assert response.status_code == 302
    assert response.url == reverse("ocpp:ocpp-dashboard")
