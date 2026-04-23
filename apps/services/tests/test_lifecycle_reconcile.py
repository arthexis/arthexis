"""Tests for lifecycle reconciliation driven by node feature state."""

from __future__ import annotations

import json

import pytest
from django.core.management import call_command
from django.test import override_settings

from gate_markers import gate
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.services.lifecycle import write_lifecycle_config
from apps.services.models import LifecycleService


pytestmark = [gate.upgrade]


@pytest.mark.django_db
@override_settings(BASE_DIR="/tmp")
def test_write_lifecycle_config_reconciles_camera_lock_from_feature_assignment(
    monkeypatch, tmp_path, settings
):
    """Feature-activated camera service should drive lockfile and unit lock output."""

    settings.BASE_DIR = tmp_path
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "service.lck").write_text("suite", encoding="utf-8")
    monkeypatch.setattr(Node, "_detect_auto_feature", lambda self, slug, **kwargs: False)

    node = Node.objects.create(
        hostname="suite-node",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
        public_endpoint="suite-node",
        base_path=str(tmp_path),
    )
    feature = NodeFeature.objects.create(slug="video-cam", display="Video Camera")
    NodeFeatureAssignment.objects.create(node=node, feature=feature)

    LifecycleService.objects.update_or_create(
        slug="camera-service",
        defaults={
            "display": "Camera capture service",
            "unit_template": "camera-{service}.service",
            "activation": LifecycleService.Activation.FEATURE,
            "feature_slug": "video-cam",
            "lock_names": ["camera-service.lck"],
        },
    )

    write_lifecycle_config(tmp_path)

    assert (lock_dir / "camera-service.lck").exists()
    assert "camera-suite.service" in (lock_dir / "systemd_services.lck").read_text(
        encoding="utf-8"
    )

    NodeFeatureAssignment.objects.filter(node=node, feature=feature).delete()
    write_lifecycle_config(tmp_path)

    assert not (lock_dir / "camera-service.lck").exists()


@pytest.mark.django_db
@override_settings(BASE_DIR="/tmp")
def test_reconcile_node_features_services_command_uses_auto_detection(monkeypatch, tmp_path, settings):
    """Reconciliation command should refresh auto features before lifecycle writes."""

    settings.BASE_DIR = tmp_path
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "service.lck").write_text("suite", encoding="utf-8")

    LifecycleService.objects.update_or_create(
        slug="camera-service",
        defaults={
            "display": "Camera capture service",
            "unit_template": "camera-{service}.service",
            "activation": LifecycleService.Activation.FEATURE,
            "feature_slug": "video-cam",
            "lock_names": ["camera-service.lck"],
        },
    )

    monkeypatch.setattr(
        Node,
        "_detect_auto_feature",
        lambda self, slug, **kwargs: slug == "video-cam",
    )

    Node.objects.create(
        hostname="auto-video-node",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
        public_endpoint="auto-video-node",
        base_path=str(tmp_path),
    )
    NodeFeature.objects.create(slug="video-cam", display="Video Camera")

    call_command("reconcile_node_features_services")

    assert (lock_dir / "camera-service.lck").exists()
    payload = json.loads((lock_dir / "lifecycle_services.json").read_text(encoding="utf-8"))
    assert "camera-suite.service" in payload["systemd_units"]
