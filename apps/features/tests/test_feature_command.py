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
def test_feature_command_requires_kind_for_toggles() -> None:
    """Enable/disable toggles require an explicit kind selector."""

    with pytest.raises(CommandError, match="--kind is required"):
        call_command("feature", "--enable", "suite-enabled")


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
