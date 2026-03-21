"""Regression coverage for retired Wikipedia Companion feature migrations."""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

migration_0037 = import_module(
    "apps.features.migrations.0037_seed_wikipedia_companion_suite_feature"
)
migration_0047 = import_module(
    "apps.features.migrations.0047_remove_wikipedia_companion_suite_feature"
)


class RecordingQuerySet:
    """Capture delete operations issued through a migration queryset."""

    def __init__(self) -> None:
        """Initialize the deletion tracker."""

        self.deleted = False

    def delete(self) -> None:
        """Record that the migration requested deletion."""

        self.deleted = True


class RecordingManager:
    """Capture filter criteria used by migration helper functions."""

    def __init__(self) -> None:
        """Initialize the manager with an empty filter log."""

        self.last_filter_kwargs: dict[str, str] | None = None
        self.queryset = RecordingQuerySet()

    def filter(self, **kwargs):
        """Record filter criteria and return a deletable queryset stub."""

        self.last_filter_kwargs = kwargs
        return self.queryset


class RecordingFeatureModel:
    """Minimal Feature stub exposing the manager API used by the migrations."""

    objects = RecordingManager()


def test_0047_removal_targets_only_mainstream_wikipedia_companion_rows() -> None:
    """Regression: 0047 should delete only the retired mainstream seed row."""

    RecordingFeatureModel.objects = RecordingManager()

    migration_0047.remove_wikipedia_companion_suite_feature(
        SimpleNamespace(get_model=lambda app_label, model_name: RecordingFeatureModel),
        schema_editor=None,
    )

    assert RecordingFeatureModel.objects.last_filter_kwargs == {
        "slug": migration_0047.FEATURE_SLUG,
        "source": migration_0047.FEATURE_SOURCE,
    }
    assert RecordingFeatureModel.objects.queryset.deleted is True


def test_0037_reverse_targets_only_mainstream_wikipedia_companion_row() -> None:
    """Regression: reversing 0037 should remove only the retired mainstream seed row."""

    RecordingFeatureModel.objects = RecordingManager()

    migration_0037.remove_seeded_wikipedia_companion_suite_feature(
        SimpleNamespace(get_model=lambda app_label, model_name: RecordingFeatureModel),
        schema_editor=None,
    )

    assert RecordingFeatureModel.objects.last_filter_kwargs == {
        "slug": migration_0037.FEATURE_SLUG,
        "source": migration_0037.FEATURE_SOURCE,
    }
    assert RecordingFeatureModel.objects.queryset.deleted is True
