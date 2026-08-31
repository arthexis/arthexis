from unittest.mock import Mock

import pytest
from django.contrib import admin
from django.urls import reverse as django_reverse
from django.utils import timezone

from apps.ocpp.admin.charge_point import admin as charge_point_admin
from apps.ocpp.admin.charge_point import views as charge_point_views
from apps.ocpp.admin.charge_point.actions import diagnostics as diagnostics_admin
from apps.ocpp.admin.charge_point.admin import ChargerAdmin as RegisteredChargerAdmin
from apps.ocpp.admin.charger import base as charger_base
from apps.ocpp.admin.charger.base import ChargerAdmin as PackagedChargerAdmin
from apps.ocpp.admin.miscellaneous import core_admin, firmware_admin
from apps.ocpp.models import Charger, CPFirmware, CPFirmwareDeployment, StationModel


def _disabled_public_route(*args, **kwargs):
    return ""


@pytest.mark.parametrize(
    ("admin_class", "admin_module"),
    (
        (RegisteredChargerAdmin, charge_point_views),
        (PackagedChargerAdmin, charger_base),
    ),
)
def test_charger_admin_dashboard_action_redirects_when_ocpp_route_is_disabled(
    rf, monkeypatch, admin_class, admin_module
):
    monkeypatch.setattr(
        admin_module, "reverse_public_ocpp_route", _disabled_public_route
    )
    request = rf.get("/")
    charger_admin = admin_class(Charger, admin.site)
    charger_admin.message_user = Mock()

    response = charger_admin.view_charge_point_dashboard(request)

    assert response.status_code == 302
    assert response["Location"] == django_reverse("admin:ocpp_charger_changelist")
    charger_admin.message_user.assert_called_once()


@pytest.mark.django_db
def test_charger_admin_page_link_is_blank_when_public_url_is_disabled(monkeypatch):
    charger = Charger.objects.create(charger_id="ADMIN-PAGE")
    charger_admin = RegisteredChargerAdmin(Charger, admin.site)
    monkeypatch.setattr(charger, "get_absolute_url", lambda: "")

    assert charger_admin.page_link(charger) == "-"


@pytest.mark.django_db
def test_charger_admin_status_link_is_blank_when_public_status_route_is_disabled(
    monkeypatch,
):
    charger = Charger.objects.create(charger_id="ADMIN-STATUS", connector_id=1)
    charger_admin = RegisteredChargerAdmin(Charger, admin.site)
    monkeypatch.setattr(
        charge_point_admin,
        "reverse_public_ocpp_route",
        _disabled_public_route,
    )

    assert charger_admin.status_link(charger) == "-"


def test_station_model_admin_view_in_site_redirects_when_public_route_is_disabled(
    rf, monkeypatch
):
    station_model_admin = core_admin.StationModelAdmin(StationModel, admin.site)
    station_model_admin.message_user = Mock()
    monkeypatch.setattr(core_admin, "reverse_public_ocpp_route", _disabled_public_route)

    response = station_model_admin.view_in_site(rf.get("/"))

    assert response.status_code == 302
    assert response["Location"] == django_reverse("admin:ocpp_stationmodel_changelist")
    station_model_admin.message_user.assert_called_once()


@pytest.mark.django_db
def test_diagnostics_setup_reports_error_when_public_upload_route_is_disabled(
    rf, monkeypatch
):
    charger = Charger.objects.create(charger_id="ADMIN-DIAG")
    charger_admin = RegisteredChargerAdmin(Charger, admin.site)
    charger_admin.message_user = Mock()
    monkeypatch.setattr(
        diagnostics_admin,
        "reverse_public_ocpp_route",
        _disabled_public_route,
    )

    charger_admin._request_get_diagnostics(
        rf.get("/"),
        Charger.objects.filter(pk=charger.pk),
        expires_at=timezone.now(),
        success_message=lambda count: "ok",
    )

    charger_admin.message_user.assert_called_once()


@pytest.mark.django_db
def test_firmware_update_reports_error_when_public_download_route_is_disabled(
    rf, monkeypatch
):
    charger = Charger.objects.create(charger_id="ADMIN-FW")
    firmware = CPFirmware.objects.create(name="Firmware", payload_json={"version": 1})
    firmware_model_admin = firmware_admin.CPFirmwareAdmin(CPFirmware, admin.site)
    firmware_model_admin.message_user = Mock()
    monkeypatch.setattr(
        firmware_admin,
        "reverse_public_ocpp_route",
        _disabled_public_route,
    )
    monkeypatch.setattr(
        firmware_admin.store,
        "get_connection",
        lambda charger_id, connector_id: object(),
    )

    updated = firmware_model_admin._dispatch_firmware_update(
        rf.get("/"),
        firmware,
        charger,
        retrieve_date=None,
        retries=None,
        retry_interval=None,
    )

    deployment = CPFirmwareDeployment.objects.get(firmware=firmware, charger=charger)
    assert updated is False
    assert deployment.status == "Error"
    firmware_model_admin.message_user.assert_called_once()
