"""Regression coverage for suite feature baseline-version defaults."""

from __future__ import annotations

import pytest

from apps.features.management.feature_ops import apply_suite_feature_baseline_defaults
from apps.features.models import Feature


@pytest.mark.django_db
def test_baseline_reached_accepts_semver_and_v_prefix() -> None:
    """Baseline gating should parse normal semantic versions and ``v`` prefixes."""

    feature = Feature.objects.create(
        slug="baseline-ready",
        display="Baseline Ready",
        baseline_version="v1.2.0",
        is_enabled=True,
    )

    assert feature.baseline_reached(current_version="1.2.0") is True
    assert feature.baseline_reached(current_version="1.1.9") is False


@pytest.mark.django_db
def test_apply_suite_feature_baseline_defaults_disables_future_only() -> None:
    """Only future-baseline suite features should be disabled by default enforcement."""

    future_feature = Feature.objects.create(
        slug="future-feature",
        display="Future Feature",
        baseline_version="9.9.9",
        is_enabled=True,
    )
    reached_feature = Feature.objects.create(
        slug="reached-feature",
        display="Reached Feature",
        baseline_version="0.1.0",
        is_enabled=True,
    )
    no_baseline_feature = Feature.objects.create(
        slug="no-baseline",
        display="No Baseline",
        baseline_version="",
        is_enabled=False,
    )

    updated_count = apply_suite_feature_baseline_defaults(current_version="1.0.0")

    future_feature.refresh_from_db()
    reached_feature.refresh_from_db()
    no_baseline_feature.refresh_from_db()

    assert updated_count == 1
    assert future_feature.is_enabled is False
    assert reached_feature.is_enabled is True
    assert no_baseline_feature.is_enabled is False
