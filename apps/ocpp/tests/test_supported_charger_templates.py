import json
from pathlib import Path

import pytest
from django.urls import reverse

from apps.media.models import MediaBucket, MediaFile
from apps.ocpp.models import StationModel
from apps.ocpp.views.public import _landing_requires_station_models


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

    response = client.get(reverse("ocpp:supported-charger-detail", args=[station_model.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "manual.pdf" in content
    assert "KB" in content
    assert 'alt="ABB Terra 54"' in content


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


def test_ocpp_module_fixture_landings_prioritize_dashboard_simulator_supported():
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
        (reverse("ocpp:cp-simulator"), "EVCS Online Simulator"),
        (reverse("ocpp:supported-chargers"), "Supported CP Models"),
    ]
