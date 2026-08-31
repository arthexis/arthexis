"""Regression tests for checkpointed release data transforms."""

from __future__ import annotations

import pytest

from apps.app.models import Application
from apps.features.models import Feature
from apps.modules.models import Module
from apps.ocpp.models.charger import Charger
from apps.ocpp.models.charging_station import ChargingStation
from apps.release.domain.data_transforms import run_transform
from apps.reports.models import SQLReport, SQLReportProduct
from apps.sites.models import Landing


def _run_until_complete(name: str, *, base_dir, limit: int = 5) -> None:
    """Run a checkpointed transform until it reports completion."""

    for _ in range(limit):
        result = run_transform(name, base_dir=base_dir)
        if result.complete:
            return
    raise AssertionError(f"Transform {name} did not complete within {limit} runs")


@pytest.mark.django_db
def test_release_transforms_normalize_modules_and_reports(tmp_path) -> None:
    """Deferred transforms should restore legacy module and report state."""

    module = Module.objects.create(path="/alpha/")
    Module.objects.filter(pk=module.pk).update(path="alpha")

    report = SQLReport.objects.create(
        name="Legacy report",
        report_type=SQLReport.ReportType.SIGIL_ROOTS,
        parameters={},
        database_alias="warehouse",
        query="SELECT 1",
        html_template_name="reports/sql/legacy.html",
        schedule_enabled=True,
        schedule_interval_minutes=30,
    )
    product = SQLReportProduct.objects.create(
        report=report,
        report_type=SQLReport.ReportType.SIGIL_ROOTS,
        parameters={"legacy": True},
        renderer_template_name="reports/sql/original.html",
        execution_details={},
        database_alias="warehouse",
        resolved_sql="SELECT 1",
        html_content="<p>ok</p>",
    )

    _run_until_complete("modules.normalize_paths", base_dir=tmp_path)
    _run_until_complete("reports.archive_sql_reports", base_dir=tmp_path)
    _run_until_complete("reports.archive_sql_report_products", base_dir=tmp_path)

    module.refresh_from_db()
    report.refresh_from_db()
    product.refresh_from_db()

    assert module.path == "/alpha/"
    assert report.report_type == SQLReport.ReportType.LEGACY_ARCHIVED
    assert report.parameters == {}
    assert report.legacy_definition == {
        "database_alias": "warehouse",
        "html_template_name": "reports/sql/legacy.html",
        "query": "SELECT 1",
    }
    assert report.schedule_enabled is False
    assert report.schedule_interval_minutes == 0
    assert product.report_type == SQLReport.ReportType.LEGACY_ARCHIVED
    assert product.parameters == {}
    assert product.renderer_template_name == "reports/sql/legacy.html"
    assert product.execution_details == {
        "database_alias": "warehouse",
        "resolved_sql": "SELECT 1",
    }


@pytest.mark.django_db
def test_release_transforms_link_ocpp_charging_stations(tmp_path) -> None:
    """Deferred transforms should link charge points to charging stations."""

    charger = Charger.objects.create(charger_id="STATION-1", connector_id=1)
    Charger.objects.filter(pk=charger.pk).update(
        charging_station=None
    )

    _run_until_complete("ocpp.link_charging_stations", base_dir=tmp_path)

    charger.refresh_from_db()

    assert charger.export_transactions is True
    assert charger.charging_station is not None
    assert ChargingStation.objects.filter(station_id="STATION-1").exists()


@pytest.mark.django_db
def test_release_transforms_remove_retired_feature_surfaces(tmp_path) -> None:
    """Deferred transforms should remove stale feature module pills."""

    docs_app = Application.objects.create(name="docs")
    gallery_app = Application.objects.create(name="gallery", is_seed_data=True)
    repos_app = Application.objects.create(name="repos")
    shop_app = Application.objects.create(name="shop")
    video_app = Application.objects.create(name="video")
    retained_app = Application.objects.create(name="retained")
    retired_modules = [
        Module.objects.create(
            application=docs_app,
            path="/apps/docs/",
            menu="Documents",
            is_seed_data=True,
        ),
        Module.objects.create(
            application=docs_app,
            path="/docs/",
            menu="Developers",
            is_seed_data=True,
        ),
        Module.objects.create(
            application=gallery_app,
            path="/gallery/",
            menu="Gallery",
            is_seed_data=True,
        ),
        Module.objects.create(
            application=repos_app,
            path="/repos/",
            menu="Repo Tracker",
            is_seed_data=True,
        ),
        Module.objects.create(
            application=shop_app,
            path="/shop/",
            menu="RFID Card Shop",
            is_seed_data=True,
        ),
        Module.objects.create(
            application=video_app,
            path="/video/",
            menu="Cameras",
            is_seed_data=True,
        ),
    ]
    retained_module = Module.objects.create(
        application=retained_app,
        path="/retained/",
        menu="Retained",
        is_seed_data=True,
    )
    for module, path, label in [
        (retired_modules[0], "/apps/docs/", "Application Documents"),
        (retired_modules[1], "/docs/library/", "Developer Documents"),
        (retired_modules[2], "/gallery/ap/", "Gallery"),
        (retired_modules[3], "/repos/tracker/", "Repo Tracker"),
        (retired_modules[4], "/shop/", "RFID Card Shop"),
        (retired_modules[5], "/video/cameras/", "Camera Gallery"),
        (retained_module, "/retained/", "Retained"),
    ]:
        Landing.objects.create(
            module=module,
            path=path,
            label=label,
            is_seed_data=True,
        )
    for slug, display in [
        ("documents", "Documents"),
        ("developer-documents", "Developer Documents"),
        ("gallery", "Gallery"),
        ("repo-tracker", "Repo Tracker"),
        ("cameras", "Cameras"),
    ]:
        Feature.objects.create(
            slug=slug,
            display=display,
            is_enabled=True,
            is_seed_data=True,
        )
    retained_feature = Feature.objects.create(
        slug="github-monitoring",
        display="GitHub Monitoring",
        is_enabled=True,
        is_seed_data=True,
    )
    Module.all_objects.filter(pk__in=[module.pk for module in retired_modules]).update(
        is_seed_data=True
    )
    Landing.all_objects.exclude(module=retained_module).update(is_seed_data=True)
    Feature.all_objects.exclude(pk=retained_feature.pk).update(is_seed_data=True)

    _run_until_complete("sites.remove_retired_feature_surfaces", base_dir=tmp_path)

    assert not Module.all_objects.filter(
        is_deleted=False,
        path__in=(
            "/apps/docs/",
            "/docs/",
            "/gallery/",
            "/repos/",
            "/shop/",
            "/video/",
        ),
    ).exists()
    assert not Landing.all_objects.filter(
        is_deleted=False,
        path__in=(
            "/apps/docs/",
            "/docs/library/",
            "/gallery/ap/",
            "/repos/tracker/",
            "/shop/",
            "/video/cameras/",
        )
    ).exists()
    assert not Feature.all_objects.filter(
        is_deleted=False,
        slug__in=(
            "documents",
            "developer-documents",
            "gallery",
            "repo-tracker",
            "cameras",
        ),
    ).exists()
    assert (
        Module.all_objects.filter(
            is_deleted=True,
            path__in=(
                "/apps/docs/",
                "/docs/",
                "/gallery/",
                "/repos/",
                "/shop/",
                "/video/",
            ),
        ).count()
        == 6
    )
    assert (
        Landing.all_objects.filter(
            is_deleted=True,
            path__in=(
                "/apps/docs/",
                "/docs/library/",
                "/gallery/ap/",
                "/repos/tracker/",
                "/shop/",
                "/video/cameras/",
            ),
        ).count()
        == 6
    )
    assert not Feature.all_objects.filter(
        slug__in=(
            "documents",
            "developer-documents",
            "gallery",
            "repo-tracker",
            "cameras",
        ),
    ).exists()
    assert not Feature.objects.filter(
        slug__in=(
            "documents",
            "developer-documents",
            "gallery",
            "repo-tracker",
            "cameras",
        ),
    ).exists()
    assert Module.all_objects.filter(pk=retained_module.pk, is_deleted=False).exists()
    assert Landing.all_objects.filter(
        module=retained_module,
        path="/retained/",
        is_deleted=False,
    ).exists()
    assert Feature.all_objects.filter(
        pk=retained_feature.pk,
        is_deleted=False,
        is_enabled=True,
    ).exists()


@pytest.mark.django_db
def test_release_transform_removes_calculator_journal_and_simulator_pills(tmp_path) -> None:
    """The public-surface cleanup includes the later retired entry points."""

    calculator_module = Module.objects.create(path="/awg/", is_seed_data=True)
    journal_module = Module.objects.create(path="/journals/", is_seed_data=True)
    ocpp_module = Module.objects.create(path="/ocpp/", is_seed_data=True)
    Landing.objects.create(
        module=calculator_module,
        path="/awg/",
        label="AWG Cable Sizing Calculator",
        is_seed_data=True,
    )
    Landing.objects.create(
        module=journal_module,
        path="/journals/",
        label="Journals",
        is_seed_data=True,
    )
    Landing.objects.create(
        module=ocpp_module,
        path="/ocpp/evcs/simulator/",
        label="EVCS Online Simulator",
        is_seed_data=True,
    )
    for slug, display in [("calculators", "Calculators"), ("journals", "Journals")]:
        Feature.objects.create(slug=slug, display=display, is_seed_data=True)

    _run_until_complete("sites.remove_retired_feature_surfaces", base_dir=tmp_path)

    assert not Module.all_objects.filter(
        is_deleted=False, path__in=("/awg/", "/journals/")
    ).exists()
    assert not Landing.all_objects.filter(
        is_deleted=False,
        path__in=("/awg/", "/journals/", "/ocpp/evcs/simulator/"),
    ).exists()
    assert not Feature.all_objects.filter(
        is_deleted=False, slug__in=("calculators", "journals")
    ).exists()
