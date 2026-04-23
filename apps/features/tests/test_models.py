"""Model-level regression coverage for suite feature lifecycle rules."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.features.models import Feature

def test_params_count_reads_feature_metadata_parameters(metadata, expected_count: int) -> None:
    """params_count should only count dictionary-backed parameter values."""

    feature = Feature.objects.create(
        slug="feature-params-count",
        display="Feature Params Count",
        metadata=metadata,
    )

    assert feature.params_count == expected_count

@pytest.mark.django_db
def test_feature_save_infers_main_app_from_code_locations() -> None:
    """Features with app-prefixed code locations should auto-link main_app."""

    feature = Feature.objects.create(
        slug="app-inference",
        display="App Inference",
        code_locations=["apps/meta/views.py"],
    )

    assert feature.main_app is not None
    assert feature.main_app.name == "meta"

