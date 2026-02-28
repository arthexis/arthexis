"""Regression tests for Watchtower AWS credential dashboard checks."""

from __future__ import annotations

from types import SimpleNamespace

from apps.aws.models import AWSCredentials
from apps.counters.dashboard_rules import evaluate_aws_credentials_rules
from apps.nodes.models import Node



def test_watchtower_requires_aws_credentials(db, monkeypatch) -> None:
    """Watchtower nodes should fail the rule when no AWS credentials exist."""

    monkeypatch.setattr(
        Node,
        "get_local",
        classmethod(lambda cls: SimpleNamespace(role_id=1, role=SimpleNamespace(name="Watchtower"))),
    )

    result = evaluate_aws_credentials_rules()

    assert result is not None
    assert result["success"] is False



def test_non_watchtower_does_not_require_aws_credentials(db, monkeypatch) -> None:
    """Non-Watchtower roles should pass regardless of AWS credentials."""

    monkeypatch.setattr(
        Node,
        "get_local",
        classmethod(lambda cls: SimpleNamespace(role_id=1, role=SimpleNamespace(name="Satellite"))),
    )

    result = evaluate_aws_credentials_rules()

    assert result is not None
    assert result["success"] is True



def test_watchtower_passes_with_credentials(db, monkeypatch) -> None:
    """Watchtower rule should pass once at least one credential exists."""

    monkeypatch.setattr(
        Node,
        "get_local",
        classmethod(lambda cls: SimpleNamespace(role_id=1, role=SimpleNamespace(name="Watchtower"))),
    )
    AWSCredentials.objects.create(
        name="Primary",
        access_key_id="AKIAOK",
        secret_access_key="secret",
    )

    result = evaluate_aws_credentials_rules()

    assert result is not None
    assert result["success"] is True
