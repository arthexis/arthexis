from __future__ import annotations

import pytest

from apps.features.models import Feature
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment, NodeRole
from apps.skills.agent_context import render_agents_context, write_agents_context
from apps.skills.models import Agent

pytestmark = [pytest.mark.django_db]


def test_render_agents_context_prioritizes_node_role_before_general_context():
    role = NodeRole.objects.create(name="Terminal")
    feature = NodeFeature.objects.create(
        slug="agent-context-feature",
        display="Agent Context Feature",
    )
    suite_feature = Feature.objects.create(
        slug="agent-context-suite",
        display="Agent Context Suite",
    )
    node = Node.objects.create(
        hostname="local-node",
        role=role,
        current_relation=Node.Relation.SELF,
    )
    NodeFeatureAssignment.objects.create(node=node, feature=feature)
    role_agent = Agent.objects.create(
        slug="role-agent",
        title="Role Agent",
        instructions="Role-specific rule.",
    )
    role_agent.node_roles.add(role)
    node_feature_agent = Agent.objects.create(
        slug="node-feature-agent",
        title="Node Feature Agent",
        instructions="Feature-specific rule.",
    )
    node_feature_agent.node_features.add(feature)
    suite_feature_agent = Agent.objects.create(
        slug="suite-feature-agent",
        title="Suite Feature Agent",
        instructions="Suite feature rule.",
    )
    suite_feature_agent.suite_features.add(suite_feature)
    Agent.objects.create(
        slug="default-agent",
        title="Default Agent",
        instructions="General rule.",
        is_default=True,
    )

    text = render_agents_context(node=node)

    assert "- Node Role: Terminal" in text
    assert text.index("Role Agent") < text.index("Node Feature Agent")
    assert text.index("Node Feature Agent") < text.index("Suite Feature Agent")
    assert text.index("Suite Feature Agent") < text.index("Default Agent")


def test_render_agents_context_tiers_by_matching_selector_not_any_selector():
    inactive_feature = NodeFeature.objects.create(
        slug="inactive-context-feature",
        display="Inactive Context Feature",
    )
    suite_feature = Feature.objects.create(
        slug="active-context-suite",
        display="Active Context Suite",
    )
    node = Node.objects.create(
        hostname="local-node",
        current_relation=Node.Relation.SELF,
    )
    mixed_agent = Agent.objects.create(
        slug="mixed-selector-agent",
        title="Mixed Selector Agent",
        instructions="Suite-specific rule.",
    )
    mixed_agent.node_features.add(inactive_feature)
    mixed_agent.suite_features.add(suite_feature)

    text = render_agents_context(node=node)

    assert "## Node Feature Context" not in text
    assert text.index("## Suite Feature Context") < text.index("Mixed Selector Agent")


def test_write_agents_context_reports_unchanged_after_first_write(tmp_path):
    target = tmp_path / "AGENTS.md"

    first = write_agents_context(target=target, node=None)
    second = write_agents_context(target=target, node=None)

    assert first.written is True
    assert second.written is False
    assert "No dynamic Agent context records" in target.read_text(encoding="utf-8")
