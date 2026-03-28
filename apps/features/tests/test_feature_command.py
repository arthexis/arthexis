"""Tests for the ``feature`` and ``features`` management commands."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from apps.features.models import Feature
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment


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
def test_features_command_reset_all_reloads_mainstream_fixtures() -> None:
    """Reset-all should mirror admin reload behavior by replacing feature rows."""

    Feature.objects.create(slug="temporary-local", display="Temporary Local", is_enabled=True)

    out = StringIO()
    call_command("features", "--reset-all", stdout=out)

    output = out.getvalue()
    assert "Dropped" in output
    assert "Reloaded" in output
    assert not Feature.objects.filter(slug="temporary-local").exists()
    assert Feature.objects.filter(slug="shortcut-management").exists()
    assert not Feature.objects.filter(slug="development-blog").exists()

