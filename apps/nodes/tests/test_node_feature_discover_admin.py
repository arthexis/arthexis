import pytest
from django.urls import reverse

from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment


@pytest.mark.django_db
def test_discover_progress_includes_manual_toggle_metadata(admin_client, monkeypatch):
    """Discover progress should expose manual toggle state for manual features."""

    node = Node.objects.create(hostname="local-node", public_endpoint="local-node")
    feature = NodeFeature.objects.create(slug="audio-capture", display="Audio Capture")
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
def test_discover_page_renders_batch_selection_controls(admin_client):
    """Discover tool view should expose selection controls before batch apply."""

    NodeFeature.objects.create(slug="gpio-rtc", display="GPIO RTC")

    response = admin_client.get(reverse("admin:nodes_nodefeature_discover"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "discover-run-selected" in content
    assert "discover-select-eligible" in content
    assert "discover-deselect-all" in content


@pytest.mark.django_db
def test_discover_manual_toggle_enables_and_disables_manual_features(admin_client, monkeypatch):
    """Manual toggle endpoint should create and remove node-feature assignments."""

    node = Node.objects.create(hostname="manual-node", public_endpoint="manual-node")
    feature = NodeFeature.objects.create(slug="audio-capture", display="Audio Capture")
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


@pytest.mark.django_db
def test_discover_progress_does_not_auto_enable_manual_features(admin_client, monkeypatch):
    """Manual features should remain unassigned after eligible discovery checks."""

    node = Node.objects.create(hostname="manual-progress", public_endpoint="manual-progress")
    feature = NodeFeature.objects.create(slug="audio-capture", display="Audio Capture")
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
    assert payload["eligible"] is True
    assert payload["enablement"]["status"] == "manual"
    assert not NodeFeatureAssignment.objects.filter(node=node, feature=feature).exists()


@pytest.mark.django_db
def test_discover_progress_auto_enables_gpio_rtc_when_eligible(admin_client, monkeypatch):
    """Auto-managed gpio-rtc should be assigned during discovery when eligible."""

    node = Node.objects.create(hostname="auto-progress", public_endpoint="auto-progress")
    feature = NodeFeature.objects.create(slug="gpio-rtc", display="GPIO RTC")
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    from django.contrib import messages

    from apps.nodes.feature_checks import FeatureCheckResult

    monkeypatch.setattr(
        "apps.nodes.feature_checks.feature_checks.run",
        lambda _feature, node=None: FeatureCheckResult(
            True,
            "RTC detected and feature eligible.",
            messages.SUCCESS,
        ),
    )

    response = admin_client.post(
        reverse("admin:nodes_nodefeature_discover_progress"),
        {"feature_id": feature.pk},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["manual_enablement"]["status"] == "auto"
    assert payload["manual_enablement"]["can_toggle"] is False
    assert payload["enablement"]["status"] == "enabled"
    assert NodeFeatureAssignment.objects.filter(node=node, feature=feature).exists()


@pytest.mark.django_db
def test_discover_progress_apply_false_reports_eligible_without_enabling(
    admin_client, monkeypatch
):
    """Eligibility preview mode should not create feature assignments."""

    node = Node.objects.create(hostname="auto-preview", public_endpoint="auto-preview")
    feature = NodeFeature.objects.create(slug="gpio-rtc", display="GPIO RTC")
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    from django.contrib import messages

    from apps.nodes.feature_checks import FeatureCheckResult

    monkeypatch.setattr(
        "apps.nodes.feature_checks.feature_checks.run",
        lambda _feature, node=None: FeatureCheckResult(
            True,
            "RTC detected and feature eligible.",
            messages.SUCCESS,
        ),
    )

    response = admin_client.post(
        reverse("admin:nodes_nodefeature_discover_progress"),
        {"feature_id": feature.pk, "apply": "false"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["eligible"] is True
    assert payload["applied"] is False
    assert payload["enablement"]["status"] == "eligible"
    assert not NodeFeatureAssignment.objects.filter(node=node, feature=feature).exists()


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
    feature = NodeFeature.objects.create(slug="audio-capture", display="Audio Capture")
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    from django.contrib import messages

    from apps.nodes.feature_checks import FeatureCheckResult

    monkeypatch.setattr(
        "apps.nodes.feature_checks.feature_checks.run",
        lambda _feature, node=None: FeatureCheckResult(
            False,
            "Recording device missing.",
            messages.WARNING,
        ),
    )

    response = admin_client.post(
        reverse("admin:nodes_nodefeature_discover_manual_toggle"),
        {"feature_id": feature.pk, "enabled": "true"},
    )

    assert response.status_code == 400
    assert not NodeFeatureAssignment.objects.filter(node=node, feature=feature).exists()
