"""Regression tests for OCPP Simulator suite feature control."""

from django.urls import reverse

import pytest

from apps.features.models import Feature
from apps.nodes.models import Node
from apps.ocpp.cpsim_service import cpsim_service_enabled


pytestmark = pytest.mark.django_db


def test_cpsim_service_enabled_reads_suite_feature_flag():
    """Regression: service enabled state should come from suite feature flag."""

    Feature.objects.update_or_create(
        slug="ocpp-simulator",
        defaults={"display": "OCPP Simulator", "is_enabled": True},
    )
    assert cpsim_service_enabled() is True


def test_simulator_admin_toggle_updates_suite_feature(admin_client, monkeypatch):
    """Regression: admin toggle should update suite feature instead of node feature assignment."""

    feature, _ = Feature.objects.update_or_create(
        slug="ocpp-simulator",
        defaults={"display": "OCPP Simulator", "is_enabled": False},
    )
    node = Node.objects.create(hostname="sim-node", public_endpoint="sim-node")
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    response = admin_client.post(reverse("admin:ocpp_simulator_cpsim_toggle"))

    assert response.status_code == 302
    feature.refresh_from_db()
    assert feature.is_enabled is True


def test_simulator_admin_toggle_requires_local_node(admin_client, monkeypatch):
    """Regression: admin toggle should fail gracefully when no local node is registered."""

    Feature.objects.update_or_create(
        slug="ocpp-simulator",
        defaults={"display": "OCPP Simulator", "is_enabled": False},
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: None))

    response = admin_client.post(reverse("admin:ocpp_simulator_cpsim_toggle"), follow=True)

    assert response.status_code == 200
    messages = [str(message) for message in response.context["messages"]]
    assert any("No local node is registered" in message for message in messages)
