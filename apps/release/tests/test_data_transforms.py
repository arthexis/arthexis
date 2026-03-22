"""Regression tests for checkpointed release data transforms."""

from __future__ import annotations

import pytest

from apps.modules.models import Module
from apps.nodes.models import Node, NodeRole
from apps.ocpp.models.charger import Charger
from apps.ocpp.models.charging_station import ChargingStation
from apps.ocpp.models.cp_forwarder import CPForwarder
from apps.release.domain.data_transforms import list_transform_names, run_transform
from apps.reports.models import SQLReport, SQLReportProduct
from apps.video.models.device import VideoDevice


def _run_until_complete(name: str, *, base_dir, limit: int = 5) -> None:
    """Run a checkpointed transform until it reports completion."""

    for _ in range(limit):
        result = run_transform(name, base_dir=base_dir)
        if result.complete:
            return
    raise AssertionError(f"Transform {name} did not complete within {limit} runs")


def test_list_transform_names_includes_deferred_migration_rollout() -> None:
    """The release transform registry should expose the rollout transforms."""

    names = list_transform_names()

    assert "modules.normalize_paths" in names
    assert "nodes.legacy_data_cleanup" in names
    assert "ocpp.enable_forwarders_and_exports" in names
    assert "ocpp.link_charging_stations" in names
    assert "reports.archive_sql_report_products" in names
    assert "reports.archive_sql_reports" in names
    assert "video.normalize_base_device_name" in names
    assert "video.populate_device_names" in names


@pytest.mark.django_db
def test_release_transforms_normalize_modules_video_and_reports(tmp_path) -> None:
    """Deferred transforms should restore legacy module, video, and report state."""

    role = NodeRole.objects.create(name="Transform Test Role")
    node = Node.objects.create(
        hostname="transform-node",
        address="127.0.0.50",
        mac_address="00:11:22:33:44:50",
        port=8050,
        public_endpoint="transform-node",
        role=role,
    )

    module = Module.objects.create(path="/alpha/")
    Module.objects.filter(pk=module.pk).update(path="alpha")

    device = VideoDevice.objects.create(node=node, identifier="cam-1")
    VideoDevice.objects.filter(pk=device.pk).update(name="BASE (migrate)", slug="")

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
    _run_until_complete("video.populate_device_names", base_dir=tmp_path)
    _run_until_complete("video.normalize_base_device_name", base_dir=tmp_path)
    _run_until_complete("reports.archive_sql_reports", base_dir=tmp_path)
    _run_until_complete("reports.archive_sql_report_products", base_dir=tmp_path)

    module.refresh_from_db()
    device.refresh_from_db()
    report.refresh_from_db()
    product.refresh_from_db()

    assert module.path == "/alpha/"
    assert device.name == VideoDevice.DEFAULT_NAME
    assert device.slug == "base"
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
def test_release_transforms_enable_ocpp_defaults_and_link_stations(tmp_path) -> None:
    """Deferred transforms should restore OCPP defaults and link charging stations."""

    role = NodeRole.objects.create(name="OCPP Transform Role")
    target_node = Node.objects.create(
        hostname="target-node",
        address="127.0.0.60",
        mac_address="00:11:22:33:44:60",
        port=8060,
        public_endpoint="target-node",
        role=role,
    )
    CPForwarder.objects.create(target_node=target_node)
    forwarder = CPForwarder.objects.get(target_node=target_node)
    CPForwarder.objects.filter(pk=forwarder.pk).update(enabled=False)

    charger = Charger.objects.create(charger_id="STATION-1", connector_id=1)
    Charger.objects.filter(pk=charger.pk).update(export_transactions=False, charging_station=None)

    _run_until_complete("ocpp.enable_forwarders_and_exports", base_dir=tmp_path)
    _run_until_complete("ocpp.link_charging_stations", base_dir=tmp_path)

    forwarder.refresh_from_db()
    charger.refresh_from_db()

    assert forwarder.enabled is True
    assert charger.export_transactions is True
    assert charger.charging_station is not None
    assert ChargingStation.objects.filter(station_id="STATION-1").exists()
