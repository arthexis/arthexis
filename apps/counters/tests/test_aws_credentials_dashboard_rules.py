"""Regression tests for Watchtower AWS credentials dashboard rule."""

from __future__ import annotations

import pytest

from apps.aws.models import AWSCredentials
from apps.counters.dashboard_rules import evaluate_aws_credentials_rules
from apps.nodes.models import Node, NodeRole


pytestmark = [pytest.mark.django_db, pytest.mark.regression]


def test_watchtower_rule_requires_at_least_one_credential(monkeypatch):
    """Watchtower nodes should fail rule evaluation when credentials are absent."""

    role = NodeRole.objects.create(name="Watchtower")
    local_node = Node.objects.create(hostname="local-watchtower", role=role)
    monkeypatch.setattr(Node, "get_local", staticmethod(lambda: local_node))

    result = evaluate_aws_credentials_rules()

    assert result["success"] is False
    assert "Configure at least one AWS credential." in result["message"]


def test_watchtower_rule_succeeds_when_credentials_exist(monkeypatch):
    """Watchtower nodes should pass when at least one credential is configured."""

    role = NodeRole.objects.create(name="Watchtower")
    local_node = Node.objects.create(hostname="local-watchtower-2", role=role)
    monkeypatch.setattr(Node, "get_local", staticmethod(lambda: local_node))
    AWSCredentials.objects.create(
        name="watchtower-key",
        access_key_id="AKIAWATCH",
        secret_access_key="secret",  # noqa: S106
    )

    result = evaluate_aws_credentials_rules()

    assert result["success"] is True


def test_non_watchtower_node_rule_succeeds(monkeypatch):
    """Non-Watchtower nodes should always pass the AWS credentials rule."""

    role = NodeRole.objects.create(name="Edge")
    local_node = Node.objects.create(hostname="local-edge", role=role)
    monkeypatch.setattr(Node, "get_local", staticmethod(lambda: local_node))

    result = evaluate_aws_credentials_rules()

    assert result["success"] is True
