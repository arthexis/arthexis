from django.urls import reverse

import pytest

from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment


@pytest.mark.django_db
def test_discover_progress_includes_manual_toggle_metadata(admin_client, monkeypatch):
    """Discover progress should expose manual toggle state for manual features."""

    node = Node.objects.create(hostname="local-node", public_endpoint="local-node")
    feature = NodeFeature.objects.create(slug="screenshot-poll", display="Screenshot Poll")
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    response = admin_client.post(
        reverse("admin:nodes_nodefeature_discover_progress"),
        {"feature_id": feature.pk},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["manual_enablement"]["status"] == "manual"
    assert payload["manual_enablement"]["can_toggle"] is True
    assert payload["manual_enablement"]["enabled"] is False


@pytest.mark.django_db
def test_discover_manual_toggle_enables_and_disables_manual_features(admin_client, monkeypatch):
    """Manual toggle endpoint should create and remove node-feature assignments."""

    node = Node.objects.create(hostname="manual-node", public_endpoint="manual-node")
    feature = NodeFeature.objects.create(slug="audio-capture", display="Audio Capture")
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    enable_response = admin_client.post(
        reverse("admin:nodes_nodefeature_discover_manual_toggle"),
        {"feature_id": feature.pk, "enabled": "true"},
    )

    assert enable_response.status_code == 200
    assert NodeFeatureAssignment.objects.filter(node=node, feature=feature).exists()

    disable_response = admin_client.post(
        reverse("admin:nodes_nodefeature_discover_manual_toggle"),
        {"feature_id": feature.pk, "enabled": "false"},
    )

    assert disable_response.status_code == 200
    assert not NodeFeatureAssignment.objects.filter(node=node, feature=feature).exists()


@pytest.mark.django_db
def test_discover_manual_toggle_rejects_non_manual_feature(admin_client, monkeypatch):
    """Manual toggle endpoint should reject auto-managed features."""

    node = Node.objects.create(hostname="auto-node", public_endpoint="auto-node")
    feature = NodeFeature.objects.create(slug="rfid-scanner", display="RFID")
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    response = admin_client.post(
        reverse("admin:nodes_nodefeature_discover_manual_toggle"),
        {"feature_id": feature.pk, "enabled": "true"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Feature is not manually controlled"
