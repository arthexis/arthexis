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
def test_set_enabled_returns_transition_state() -> None:
    """set_enabled should report whether a state transition happened."""

    feature = Feature.objects.create(slug="transition-feature", display="Transition", is_enabled=True)

    assert feature.set_enabled(True) is False
    assert feature.set_enabled(False) is True


@pytest.mark.django_db
def test_set_enabled_persists_when_update_fields_is_empty() -> None:
    """set_enabled should still persist core fields when callers pass an empty update list."""

    feature = Feature.objects.create(slug="empty-update-fields", display="Empty Update Fields", is_enabled=True)

    assert feature.set_enabled(False, update_fields=[]) is True

    feature.refresh_from_db()
    assert feature.is_enabled is False
