import json
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.urls import NoReverseMatch, reverse

from apps.media.models import MediaBucket, MediaFile
from apps.ocpp.models import Charger, StationModel
from apps.ocpp.views.public import _landing_requires_station_models


def _create_public_charger() -> Charger:
    return Charger.objects.create(
        charger_id="PUBLIC-CP",
        connector_id=1,
        authorization_policy=Charger.AuthorizationPolicy.OPEN,
        last_status="Available",
    )


def test_charger_absolute_url_returns_empty_when_public_routes_are_disabled():
    charger = Charger(charger_id="PUBLIC-CP", connector_id=1)

    with patch(
        "apps.ocpp.models.charger.reverse",
        side_effect=NoReverseMatch("route provider disabled"),
    ) as reverse_mock:
        assert charger.get_absolute_url() == ""
        assert charger._full_url() == ""

    assert [call.args[0] for call in reverse_mock.call_args_list] == [
        "ocpp:charger-page-connector",
        "charger-page-connector",
        "ocpp:charger-page-connector",
        "charger-page-connector",
    ]


@pytest.mark.django_db
def test_supported_chargers_filter_data_attributes_keep_source_casing(client):
    """Vendor and OCPP data attributes should preserve readable casing for chip labels."""

    StationModel.objects.create(
        vendor="ABB",
        model_family="Terra",
        model="54",
        preferred_ocpp_version="OCPP 1.6J",
        integration_rating=4,
    )

    response = client.get(reverse("ocpp:supported-chargers"))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'data-vendor="ABB"' in content
    assert 'data-ocpp="OCPP 1.6J"' in content


@pytest.mark.django_db
def test_supported_charger_detail_renders_without_storage_blob_for_document(client):
    """Detail page should render even when the storage blob behind a document row is missing."""

    bucket = MediaBucket.objects.create(name="Docs")
    images_bucket = MediaBucket.objects.create(name="Images")
    station_model = StationModel.objects.create(
        vendor="ABB",
        model_family="Terra",
        model="54",
        documents_bucket=bucket,
        images_bucket=images_bucket,
        integration_rating=4,
    )
    MediaFile.objects.create(
        bucket=images_bucket,
        file="protocols/buckets/images/charger.jpg",
        original_name="charger.jpg",
        content_type="image/jpeg",
        size=4096,
    )
    MediaFile.objects.create(
        bucket=bucket,
        file="protocols/buckets/docs/missing-manual.pdf",
        original_name="manual.pdf",
        content_type="application/pdf",
        size=2048,
    )

    response = client.get(
        reverse("ocpp:supported-charger-detail", args=[station_model.pk])
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "manual.pdf" in content
    assert "KB" in content
    assert 'alt="ABB Terra 54"' in content


@pytest.mark.django_db
def test_supported_charger_detail_renders_configuration_guides(client):
    station_model = StationModel.objects.create(
        vendor="Delta",
        model_family="DC",
        model="Fast",
        integration_rating=5,
    )
    guide = station_model.configuration_guides.create(
        title="Commissioning",
        firmware_version="v1.2.3",
        notes="Use installer mode.",
    )
    guide.steps.create(
        step_number=1,
        title="Open settings",
        instructions_markdown="Navigate to **Installer** settings.",
    )

    response = client.get(
        reverse("ocpp:supported-charger-detail", args=[station_model.pk])
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "Configuration Guides" in content
    assert "Commissioning" in content
    assert "v1.2.3" in content
    assert "Open settings" in content


@pytest.mark.django_db
def test_supported_charger_detail_has_no_simulator_action_for_staff(admin_client):
    station_model = StationModel.objects.create(
        vendor="IOCHARGER",
        model_family="IOCJY2",
        model="IOC750200A-T08",
        integration_rating=5,
    )

    response = admin_client.get(
        reverse("ocpp:supported-charger-detail", args=[station_model.pk])
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "Start Simulator" not in content
    assert "Simulator slot" not in content


@pytest.mark.django_db
def test_charger_public_page_has_no_simulator_panel(admin_client):
    charger = _create_public_charger()

    response = admin_client.get(
        reverse(
            "ocpp:charger-page-connector",
            args=[charger.charger_id, charger.connector_slug],
        )
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "charger-simulator-panel" not in content
    assert 'value="start-simulator"' not in content


@pytest.mark.django_db
def test_supported_chargers_landing_validator_uses_station_models(rf):
    request = rf.get(reverse("ocpp:supported-chargers"))

    assert _landing_requires_station_models(request=request, landing=None) is False

    StationModel.objects.create(
        vendor="ABB",
        model_family="Terra",
        model="54",
        integration_rating=4,
    )

    assert _landing_requires_station_models(request=request, landing=None) is True


def test_supported_chargers_fixture_path_matches_named_route():
    fixture_path = Path("apps/sites/fixtures/default__modules_terminal.json")
    fixture_data = json.loads(fixture_path.read_text())

    supported_landing = next(
        item
        for item in fixture_data
        if item.get("model") == "pages.landing"
        and item.get("fields", {}).get("label") == "Supported CP Models"
    )

    assert supported_landing["fields"]["path"] == reverse("ocpp:supported-chargers")


@pytest.mark.django_db
def test_ev_ready_ies_wallbox_fixture_is_available():
    fixture_path = Path(
        "apps/ocpp/fixtures/station_models__ev_ready__ies_wallbox_g3_ccs.json"
    )
    fixture_data = json.loads(fixture_path.read_text())

    fields = fixture_data[0]["fields"]

    assert fields["vendor"] == "EV Ready / IES"
    assert fields["model_family"] == "wallbox G3"
    assert fields["model"] == "wallbox G3 CCS"
    assert fields["max_power_kw"] == "24.00"
    assert fields["connector_type"] == "CCS"
    assert fields["preferred_ocpp_version"] == "OCPP 1.6J"
    assert fields["integration_rating"] == 3
    assert fields["is_seed_data"] is True

    call_command("loaddata", str(fixture_path), verbosity=0)
    station_model = StationModel.objects.get(
        vendor="EV Ready / IES",
        model_family="wallbox G3",
        model="wallbox G3 CCS",
    )
    assert str(station_model.max_power_kw) == "24.00"
    assert station_model.connector_type == "CCS"
    assert station_model.preferred_ocpp_version == "OCPP 1.6J"


def test_ocpp_module_fixture_landings_prioritize_dashboard_energy_supported():
    fixture_path = Path("apps/sites/fixtures/default__modules_terminal.json")
    fixture_data = json.loads(fixture_path.read_text())

    ocpp_landings = [
        (item["fields"]["path"], item["fields"]["label"])
        for item in fixture_data
        if item.get("model") == "pages.landing"
        and item.get("fields", {}).get("module") == ["/ocpp/"]
    ]

    assert ocpp_landings == [
        (reverse("ocpp:ocpp-dashboard"), "Charging Station Dashboards"),
        (reverse("ocpp:energy-reports"), "Energy Reports"),
        (reverse("ocpp:supported-chargers"), "Supported CP Models"),
    ]
