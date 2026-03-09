"""Tests for the ``feature`` and ``features`` management commands."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.features.models import Feature
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment


@pytest.mark.django_db
def test_feature_command_toggles_single_suite_feature_with_positional_slug() -> None:
    """Regression: singular command should toggle suite state via positional slug."""

    feature = Feature.objects.create(slug="suite-only-toggle", display="Suite Only", is_enabled=True)

    out = StringIO()
    call_command("feature", feature.slug, "--disabled", stdout=out)
    feature.refresh_from_db()

    assert feature.is_enabled is False
    assert f"- {feature.slug} [disabled]" in out.getvalue()


@pytest.mark.django_db
def test_feature_command_toggles_single_node_feature_with_positional_slug() -> None:
    """Singular command should toggle node feature assignment via positional slug."""

    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    feature = NodeFeature.objects.create(slug="camera-operator", display="Camera Operator")

    call_command("feature", feature.slug, "--enabled")
    assert NodeFeatureAssignment.objects.filter(node=node, feature=feature).exists()


@pytest.mark.django_db
def test_feature_command_requires_single_status_flag() -> None:
    """Singular command should reject mutually exclusive status flags."""

    feature = Feature.objects.create(slug="public-api", display="Public API", is_enabled=False)

    with pytest.raises(CommandError, match="Choose only one of --enabled or --disabled"):
        call_command("feature", feature.slug, "--enabled", "--disabled")


@pytest.mark.django_db
def test_feature_command_requires_kind_for_ambiguous_slugs() -> None:
    """Ambiguous slugs should require explicit --kind when using singular command."""

    Feature.objects.create(slug="ambiguous-toggle", display="Ambiguous Suite", is_enabled=False)
    NodeFeature.objects.create(slug="ambiguous-toggle", display="Ambiguous Node")

    with pytest.raises(CommandError, match="exists in both suite and node kinds"):
        call_command("feature", "ambiguous-toggle", "--enabled")


@pytest.mark.django_db
def test_features_command_lists_enabled_suite_and_node_features_by_default() -> None:
    """Plural command should list enabled suite/node features by default."""

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
    call_command("features", stdout=out)

    output = out.getvalue()
    assert "Suite features" in output
    assert f"- {suite_enabled.slug} [enabled]" in output
    assert "suite-disabled" not in output
    assert "Node features" in output
    assert "- node-enabled [enabled]" in output
    assert "node-disabled" not in output


@pytest.mark.django_db
def test_features_command_filters_disabled_rows() -> None:
    """Plural command should list only disabled rows when --disabled is set."""

    Feature.objects.create(slug="suite-enabled", display="Suite Enabled", is_enabled=True)
    disabled_feature = Feature.objects.create(
        slug="suite-disabled", display="Suite Disabled", is_enabled=False
    )

    out = StringIO()
    call_command("features", "--kind", "suite", "--disabled", stdout=out)

    output = out.getvalue()
    assert f"- {disabled_feature.slug} [disabled]" in output
    assert "suite-enabled" not in output


@pytest.mark.django_db
def test_features_command_with_feature_flag_behaves_like_singular_toggle() -> None:
    """Using ``--feature`` plus status filter should toggle one selected feature."""

    feature = Feature.objects.create(slug="single-filtered", display="Single Filtered", is_enabled=False)

    out = StringIO()
    call_command("features", "--feature", feature.slug, "--enabled", stdout=out)
    feature.refresh_from_db()

    assert feature.is_enabled is True
    assert f"- {feature.slug} [enabled]" in out.getvalue()


@pytest.mark.django_db
def test_features_command_with_feature_rejects_mutually_exclusive_status_flags() -> None:
    """Plural command should reject conflicting status flags for ``--feature`` mode."""

    feature = Feature.objects.create(slug="conflict-slug", display="Conflict", is_enabled=False)

    with pytest.raises(CommandError, match="Choose only one of --enabled or --disabled"):
        call_command("features", "--feature", feature.slug, "--enabled", "--disabled")

    feature.refresh_from_db()
    assert feature.is_enabled is False


@pytest.mark.django_db
def test_features_command_reset_all_reloads_mainstream_fixtures() -> None:
    """Reset-all should mirror admin reload behavior by replacing feature rows."""

    Feature.objects.create(slug="temporary-local", display="Temporary Local", is_enabled=True)

    out = StringIO()
    call_command("features", "--reset-all", stdout=out)

    output = out.getvalue()
    assert "Dropped" in output
    assert "Reloaded" in output
    assert not Feature.objects.filter(slug="temporary-local").exists()
    assert Feature.objects.filter(slug="development-blog").exists()
