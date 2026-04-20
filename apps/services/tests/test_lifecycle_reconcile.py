"""Tests for lifecycle reconciliation driven by node feature state."""

from __future__ import annotations

import json

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.summary.models import LLMSummaryConfig
from apps.summary.services import get_summary_config
from apps.services.lifecycle import write_lifecycle_config
from apps.services.models import LifecycleService


@pytest.mark.django_db
@override_settings(BASE_DIR="/tmp")
def test_write_lifecycle_config_reconciles_camera_lock_from_feature_assignment(tmp_path, settings):
    """Feature-activated camera service should drive lockfile and unit lock output."""

    settings.BASE_DIR = tmp_path
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "service.lck").write_text("suite", encoding="utf-8")

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

    Node.objects.create(
        hostname="auto-video-node",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
        public_endpoint="auto-video-node",
        base_path=str(tmp_path),
    )
    NodeFeature.objects.create(slug="video-cam", display="Video Camera")

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

    call_command("reconcile_node_features_services")

    assert (lock_dir / "camera-service.lck").exists()
    payload = json.loads((lock_dir / "lifecycle_services.json").read_text(encoding="utf-8"))
    assert "camera-suite.service" in payload["systemd_units"]


@pytest.mark.django_db
@override_settings(BASE_DIR="/tmp")
def test_reconcile_node_features_services_command_tracks_summary_runtime_lock(tmp_path, settings):
    """Summary model selection should reconcile the managed runtime lock and unit list."""

    settings.BASE_DIR = tmp_path
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "service.lck").write_text("suite", encoding="utf-8")

    LifecycleService.objects.update_or_create(
        slug="summary-runtime",
        defaults={
            "display": "LLM Summary Runtime",
            "unit_template": "summary-runtime-{service}.service",
            "activation": LifecycleService.Activation.LOCKFILE,
            "lock_names": ["summary-runtime-service.lck"],
        },
    )

    config = get_summary_config()
    config.backend = LLMSummaryConfig.Backend.LLAMA_CPP_SERVER
    config.selected_model = "gemma-4-e2b-it"
    config.runtime_base_url = "http://127.0.0.1:8080/v1"
    config.runtime_binary_path = "llama-server"
    config.is_active = True
    config.save(
        update_fields=[
            "backend",
            "selected_model",
            "runtime_base_url",
            "runtime_binary_path",
            "is_active",
            "updated_at",
        ]
    )

    call_command("reconcile_node_features_services")

    assert (lock_dir / "summary-runtime-service.lck").exists()
    payload = json.loads((lock_dir / "lifecycle_services.json").read_text(encoding="utf-8"))
    assert "summary-runtime-suite.service" in payload["systemd_units"]
