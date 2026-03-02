"""Tests for the `feature` management command."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.features.models import Feature
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment


@pytest.mark.django_db
def test_feature_command_lists_enabled_suite_and_node_features_by_default() -> None:
    """Default output should include enabled suite and enabled node features."""

    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    suite_enabled = Feature.objects.create(slug="suite-enabled", display="Suite Enabled", is_enabled=True)
    Feature.objects.create(slug="suite-disabled", display="Suite Disabled", is_enabled=False)
    node_feature_enabled = NodeFeature.objects.create(slug="node-enabled", display="Node Enabled")
    NodeFeature.objects.create(slug="node-disabled", display="Node Disabled")
    NodeFeatureAssignment.objects.create(node=node, feature=node_feature_enabled)

    out = StringIO()
    call_command("feature", stdout=out)

    output = out.getvalue()
    assert "Suite features" in output
    assert f"- {suite_enabled.slug} [enabled]" in output
    assert "suite-disabled" not in output
    assert "Node features" in output
    assert "- node-enabled [enabled]" in output
    assert "node-disabled" not in output


@pytest.mark.django_db
def test_feature_command_lists_all_for_selected_kind() -> None:
    """`--kind node --all` should list both enabled and disabled node features."""

    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    enabled = NodeFeature.objects.create(slug="gpio-rtc", display="GPIO RTC")
    disabled = NodeFeature.objects.create(slug="video-cam", display="Video Camera")
    NodeFeatureAssignment.objects.create(node=node, feature=enabled)

    out = StringIO()
    call_command("feature", "--kind", "node", "--all", stdout=out)

    output = out.getvalue()
    assert "Suite features" not in output
    assert f"- {enabled.slug} [enabled]" in output
    assert f"- {disabled.slug} [disabled]" in output


@pytest.mark.django_db
def test_feature_command_requires_kind_for_ambiguous_toggles() -> None:
    """Enable/disable toggles require --kind when slug exists in both catalogs."""

    Feature.objects.create(slug="ambiguous-toggle", display="Ambiguous Suite", is_enabled=False)
    NodeFeature.objects.create(slug="ambiguous-toggle", display="Ambiguous Node")

    with pytest.raises(CommandError, match="exists in both suite and node kinds"):
        call_command("feature", "--enable", "ambiguous-toggle")


@pytest.mark.django_db
def test_feature_command_infers_suite_kind_for_toggle() -> None:
    """Toggle operations should infer suite kind when slug is suite-only."""

    feature = Feature.objects.create(slug="suite-only-toggle", display="Suite Only", is_enabled=True)

    call_command("feature", "--disable", feature.slug)
    feature.refresh_from_db()
    assert feature.is_enabled is False


@pytest.mark.django_db
def test_feature_command_infers_node_kind_for_toggle() -> None:
    """Toggle operations should infer node kind when slug is node-only."""

    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    feature = NodeFeature.objects.create(slug="camera-operator", display="Camera Operator")

    call_command("feature", "--enable", feature.slug)
    assert NodeFeatureAssignment.objects.filter(node=node, feature=feature).exists()


@pytest.mark.django_db
def test_feature_command_unknown_slug_without_kind_reports_helpful_error() -> None:
    """Unknown slug without --kind should raise a specific validation error."""

    with pytest.raises(CommandError, match="Unknown feature 'missing-feature'"):
        call_command("feature", "--disable", "missing-feature")


@pytest.mark.django_db
def test_feature_command_enables_and_disables_suite_feature() -> None:
    """Suite feature toggles should update the global feature flag."""

    feature = Feature.objects.create(slug="public-api", display="Public API", is_enabled=False)

    call_command("feature", "--kind", "suite", "--enable", feature.slug)
    feature.refresh_from_db()
    assert feature.is_enabled is True

    call_command("feature", "--kind", "suite", "--disable", feature.slug)
    feature.refresh_from_db()
    assert feature.is_enabled is False


@pytest.mark.django_db
def test_feature_command_enables_and_disables_node_feature() -> None:
    """Node feature toggles should manage local node assignments."""

    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    feature = NodeFeature.objects.create(slug="audio-capture", display="Audio Capture")

    call_command("feature", "--kind", "node", "--enable", feature.slug)
    assert NodeFeatureAssignment.objects.filter(node=node, feature=feature).exists()

    call_command("feature", "--kind", "node", "--disable", feature.slug)
    assert not NodeFeatureAssignment.objects.filter(node=node, feature=feature).exists()
