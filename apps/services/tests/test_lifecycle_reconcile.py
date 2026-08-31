"""Tests for lifecycle reconciliation driven by node feature state."""

from __future__ import annotations

import json

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.nodes.feature_detection import node_feature_detection_registry
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment, NodeRole
from apps.services import lifecycle
from apps.services.lifecycle import write_lifecycle_config
from apps.services.models import LifecycleService
from gate_markers import gate

pytestmark = [gate.upgrade]


@pytest.mark.django_db
@override_settings(BASE_DIR="/tmp")
def test_write_lifecycle_config_reconciles_lcd_lock_from_feature_assignment(
    monkeypatch, tmp_path, settings
):
    """Feature-activated LCD service should drive lockfile and unit lock output."""

    settings.BASE_DIR = tmp_path
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "service.lck").write_text("suite", encoding="utf-8")
    monkeypatch.setattr(
        node_feature_detection_registry, "detect", lambda slug, **kwargs: False
    )

    role = NodeRole.objects.create(name="Control")
    node = Node.objects.create(
        hostname="suite-node",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
        public_endpoint="suite-node",
        base_path=str(tmp_path),
        role=role,
    )
    feature = NodeFeature.objects.create(slug="lcd-screen", display="LCD Screen")
    NodeFeatureAssignment.objects.create(node=node, feature=feature)

    LifecycleService.objects.update_or_create(
        slug="lcd-service",
        defaults={
            "display": "LCD service",
            "unit_template": "lcd-{service}.service",
            "activation": LifecycleService.Activation.FEATURE,
            "feature_slug": "lcd-screen",
            "lock_names": ["lcd-service.lck"],
        },
    )

    write_lifecycle_config(tmp_path)

    assert (lock_dir / "lcd-service.lck").exists()
    assert "lcd-suite.service" in (lock_dir / "systemd_services.lck").read_text(
        encoding="utf-8"
    )

    NodeFeatureAssignment.objects.filter(node=node, feature=feature).delete()
    write_lifecycle_config(tmp_path)

    assert not (lock_dir / "lcd-service.lck").exists()


@pytest.mark.django_db
@override_settings(BASE_DIR="/tmp")
def test_reconcile_node_features_services_command_uses_auto_detection(
    monkeypatch, tmp_path, settings
):
    """Reconciliation command should refresh auto features before lifecycle writes."""

    settings.BASE_DIR = tmp_path
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "service.lck").write_text("suite", encoding="utf-8")

    LifecycleService.objects.update_or_create(
        slug="lcd-service",
        defaults={
            "display": "LCD service",
            "unit_template": "lcd-{service}.service",
            "activation": LifecycleService.Activation.FEATURE,
            "feature_slug": "lcd-screen",
            "lock_names": ["lcd-service.lck"],
        },
    )

    monkeypatch.setattr(
        node_feature_detection_registry,
        "detect",
        lambda slug, **kwargs: slug == "lcd-screen",
    )

    role = NodeRole.objects.create(name="Control")
    Node.objects.create(
        hostname="auto-lcd-node",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
        public_endpoint="auto-lcd-node",
        base_path=str(tmp_path),
        role=role,
    )
    NodeFeature.objects.create(slug="lcd-screen", display="LCD Screen")

    call_command("reconcile_node_features_services")

    assert (lock_dir / "lcd-service.lck").exists()
    payload = json.loads(
        (lock_dir / "lifecycle_services.json").read_text(encoding="utf-8")
    )
    assert "lcd-suite.service" in payload["systemd_units"]


@pytest.mark.django_db
@override_settings(BASE_DIR="/tmp")
def test_reconcile_node_features_services_repairs_celery_feature_drift(
    monkeypatch, tmp_path, settings
):
    """Reconciliation command should repair celery feature drift from auto-detection."""

    settings.BASE_DIR = tmp_path
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "service.lck").write_text("suite", encoding="utf-8")

    role = NodeRole.objects.create(name="Control")
    node = Node.objects.create(
        hostname="celery-node",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
        public_endpoint="celery-node",
        base_path=str(tmp_path),
        role=role,
    )
    NodeFeature.objects.create(
        slug="celery-queue",
        display="Celery Queue",
        footprint=NodeFeature.Footprint.HEAVY,
    )

    monkeypatch.setattr(Node, "get_local", staticmethod(lambda: node))
    monkeypatch.setattr(
        node_feature_detection_registry,
        "detect",
        lambda slug, **kwargs: slug == "celery-queue",
    )

    call_command("reconcile_node_features_services")

    assert node.features.filter(slug="celery-queue").exists()


@pytest.mark.django_db
@override_settings(BASE_DIR="/tmp")
def test_lifecycle_config_filters_stale_systemd_lock_extras_when_services_exist(
    monkeypatch, tmp_path, settings
):
    """Reconciliation keeps installed extras but drops missing retired units."""

    settings.BASE_DIR = tmp_path
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "service.lck").write_text("suite", encoding="utf-8")
    (lock_dir / "systemd_services.lck").write_text(
        "\n".join(
            [
                "suite.service",
                "rfid-suite.service",
                "lcd-suite.service",
                "celery-suite.service",
                "celery-beat-suite.service",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        lifecycle,
        "_systemd_unit_exists",
        lambda unit_name: unit_name == "rfid-suite.service",
    )
    LifecycleService.objects.update_or_create(
        slug="suite",
        defaults={
            "display": "Suite",
            "unit_template": "{service}.service",
            "activation": LifecycleService.Activation.ALWAYS,
        },
    )

    payload = write_lifecycle_config(tmp_path)
    written_units = (lock_dir / "systemd_services.lck").read_text(encoding="utf-8")

    assert payload.systemd_units == ["suite.service", "rfid-suite.service"]
    assert written_units == "suite.service\nrfid-suite.service\n"


@pytest.mark.django_db
@override_settings(BASE_DIR="/tmp")
def test_lifecycle_config_preserves_systemd_lock_when_no_services_exist(
    tmp_path, settings
):
    """The previous lock remains a fallback when lifecycle rows are unavailable."""

    settings.BASE_DIR = tmp_path
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "systemd_services.lck").write_text(
        "legacy.service\nlegacy.timer\n",
        encoding="utf-8",
    )

    payload = write_lifecycle_config(tmp_path)

    assert payload.systemd_units == ["legacy.service", "legacy.timer"]


@pytest.mark.django_db
@override_settings(BASE_DIR="/tmp")
def test_lifecycle_config_preserves_timer_unit_kind(monkeypatch, tmp_path, settings):
    """Timer lifecycle rows should render as timers, not service defaults."""

    settings.BASE_DIR = tmp_path
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    role = NodeRole.objects.create(name="Control")
    node = Node.objects.create(
        hostname="control-node",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
        public_endpoint="control-node",
        base_path=str(tmp_path),
        role=role,
    )
    feature = NodeFeature.objects.create(slug="usb-inventory", display="USB Inventory")
    NodeFeatureAssignment.objects.create(node=node, feature=feature)
    monkeypatch.setattr(Node, "get_local", staticmethod(lambda: node))
    LifecycleService.objects.update_or_create(
        slug="usb-inventory-timer",
        defaults={
            "display": "USB Inventory Timer",
            "unit_template": "arthexis-usb-inventory",
            "unit_kind": LifecycleService.UnitKind.TIMER,
            "activation": LifecycleService.Activation.FEATURE,
            "feature_slug": "usb-inventory",
        },
    )

    payload = write_lifecycle_config(tmp_path)

    assert "arthexis-usb-inventory.timer" in payload.systemd_units
    timer = next(
        service
        for service in payload.services
        if service["key"] == "usb-inventory-timer"
    )
    assert timer["unit_kind"] == LifecycleService.UnitKind.TIMER
    assert timer["unit"] == "arthexis-usb-inventory.timer"
    assert timer["unit_display"] == "arthexis-usb-inventory.timer"


@pytest.mark.django_db
@override_settings(BASE_DIR="/tmp")
def test_lifecycle_config_includes_imager_burner_service_for_feature(
    monkeypatch, tmp_path, settings
):
    """The imager-burner node feature should drive the durable worker unit."""

    settings.BASE_DIR = tmp_path
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "service.lck").write_text("suite", encoding="utf-8")
    role = NodeRole.objects.create(name="Control")
    node = Node.objects.create(
        hostname="burner-node",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
        public_endpoint="burner-node",
        base_path=str(tmp_path),
        role=role,
    )
    feature = NodeFeature.objects.create(slug="imager-burner", display="Imager Burner")
    NodeFeatureAssignment.objects.create(node=node, feature=feature)
    monkeypatch.setattr(Node, "get_local", staticmethod(lambda: node))
    LifecycleService.objects.update_or_create(
        slug="imager-burner",
        defaults={
            "display": "Imager Burner",
            "unit_template": "arthexis-imager-burner.service",
            "activation": LifecycleService.Activation.FEATURE,
            "feature_slug": "imager-burner",
        },
    )

    payload = write_lifecycle_config(tmp_path)

    assert "arthexis-imager-burner.service" in payload.systemd_units


def test_lifecycle_service_name_resolution_only_replaces_service_placeholder():
    """Unexpected brace tokens should not break lifecycle reconciliation."""

    service = LifecycleService(unit_template="demo-{service}-{unknown}.service")

    assert service.resolved_unit_name("suite") == "demo-suite-{unknown}.service"
