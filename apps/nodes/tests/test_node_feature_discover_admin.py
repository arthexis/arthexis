import pytest
from django.urls import reverse

from apps.discovery.models import DiscoveryItem
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment

@pytest.mark.django_db
def test_discover_progress_includes_manual_toggle_metadata(admin_client, monkeypatch):
    """Discover progress should expose manual toggle state for manual features."""

    node = Node.objects.create(hostname="local-node", public_endpoint="local-node")
    feature = NodeFeature.objects.create(slug="screenshot-poll", display="Screenshot Poll")
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    from django.contrib import messages

    from apps.nodes.feature_checks import FeatureCheckResult

    monkeypatch.setattr(
        "apps.nodes.feature_checks.feature_checks.run",
        lambda _feature, node=None: FeatureCheckResult(
            True,
            "Eligible for manual enablement.",
            messages.SUCCESS,
        ),
    )

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
def test_discover_progress_does_not_auto_enable_gpio_rtc_when_ineligible(admin_client, monkeypatch):
    """Auto-managed gpio-rtc should stay unassigned during discovery when ineligible."""

    node = Node.objects.create(hostname="auto-progress", public_endpoint="auto-progress")
    feature = NodeFeature.objects.create(slug="gpio-rtc", display="GPIO RTC")
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    from django.contrib import messages

    from apps.nodes.feature_checks import FeatureCheckResult

    monkeypatch.setattr(
        "apps.nodes.feature_checks.feature_checks.run",
        lambda _feature, node=None: FeatureCheckResult(
            False,
            "RTC not detected.",
            messages.WARNING,
        ),
    )

    response = admin_client.post(
        reverse("admin:nodes_nodefeature_discover_progress"),
        {"feature_id": feature.pk},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["eligible"] is False
    assert payload["manual_enablement"]["status"] == "auto"
    assert payload["manual_enablement"]["can_toggle"] is False
    assert payload["enablement"]["status"] == "skipped"
    assert not NodeFeatureAssignment.objects.filter(node=node, feature=feature).exists()

@pytest.mark.django_db
def test_discover_manual_toggle_blocks_when_ineligible(admin_client, monkeypatch):
    """Regression: manual toggle should be blocked when eligibility check fails."""

    node = Node.objects.create(hostname="manual-blocked", public_endpoint="manual-blocked")
    feature = NodeFeature.objects.create(slug="screenshot-poll", display="Screenshot Poll")
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    from django.contrib import messages

    from apps.nodes.feature_checks import FeatureCheckResult

    monkeypatch.setattr(
        "apps.nodes.feature_checks.feature_checks.run",
        lambda _feature, node=None: FeatureCheckResult(
            False,
            "Screenshot prerequisites missing.",
            messages.WARNING,
        ),
    )

    response = admin_client.post(
        reverse("admin:nodes_nodefeature_discover_manual_toggle"),
        {"feature_id": feature.pk, "enabled": "true"},
    )

    assert response.status_code == 400
    assert not NodeFeatureAssignment.objects.filter(node=node, feature=feature).exists()
