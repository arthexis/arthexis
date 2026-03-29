"""Regression tests for applying upgrade policies."""

from __future__ import annotations

import pytest

from apps.nodes.tasks import apply_upgrade_policies

pytestmark = [pytest.mark.django_db, pytest.mark.regression]


def test_apply_upgrade_policies_skips_when_auto_upgrade_feature_disabled(monkeypatch):
    """Feature toggle should skip scheduled policy checks."""

    monkeypatch.setattr("apps.nodes.tasks.auto_upgrade_suite_feature_enabled", lambda default=True: False)

    result = apply_upgrade_policies()

    assert result == "skipped:feature-disabled"
