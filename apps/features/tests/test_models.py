"""Model-level regression coverage for suite feature lifecycle rules."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.features.models import Feature


@pytest.mark.django_db
def test_enabled_suite_feature_cannot_be_deleted() -> None:
    """Regression: enabled suite features must be disabled before deletion."""

    feature = Feature.objects.create(slug="guarded-delete", display="Guarded Delete", is_enabled=True)

    with pytest.raises(ValidationError, match="Disable this suite feature before deleting it"):
        feature.delete()


@pytest.mark.django_db
def test_disabled_suite_feature_can_be_deleted() -> None:
    """Disabled suite features should remain deletable."""

    feature = Feature.objects.create(
        slug="deletable-disabled", display="Deletable Disabled", is_enabled=False
    )

    feature.delete()

    assert not Feature.all_objects.filter(pk=feature.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("metadata", "expected_count"),
    [
        ({"parameters": {"one": "1", "two": "2"}}, 2),
        ({"parameters": {}}, 0),
        ({"parameters": "not-a-dict"}, 0),
        ({"parameters": None}, 0),
        ({}, 0),
    ],
)
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


@pytest.mark.django_db
def test_set_enabled_persists_inferred_main_app_with_update_fields() -> None:
    """set_enabled should persist inferred main_app even with scoped update_fields."""

    feature = Feature.objects.create(
        slug="app-inference-update-fields",
        display="App Inference Update Fields",
        code_locations=[],
    )
    Feature.objects.filter(pk=feature.pk).update(code_locations=["apps/meta/views.py"])
    feature.refresh_from_db()

    assert feature.main_app_id is None
    assert feature.set_enabled(False, update_fields=["is_enabled"]) is True

    feature.refresh_from_db()
    assert feature.main_app is not None
    assert feature.main_app.name == "meta"
