"""Regression coverage for the LLM summary feature command archival migration."""

from __future__ import annotations

from importlib import import_module


migration_0052 = import_module(
    "apps.features.migrations.0052_archive_llm_summary_model_command_parameter"
)


class StubFeature:
    """Minimal feature object used to exercise migration metadata updates."""

    def __init__(self, metadata: dict[str, object]) -> None:
        """Store mutable metadata and initialize save tracking.

        Parameters:
            metadata: Initial feature metadata payload used by the migration.

        Returns:
            None.
        """

        self.metadata = metadata
        self.saved_update_fields: list[str] | None = None

    def save(self, *, update_fields: list[str]) -> None:
        """Record the update fields used by the migration helper.

        Parameters:
            update_fields: Model fields requested for persistence.

        Returns:
            None.
        """

        self.saved_update_fields = update_fields


class StubFeatureQuerySet:
    """Return a predefined feature from the migration's filter().first() chain."""

    def __init__(self, feature: StubFeature | None) -> None:
        """Initialize the query set stub.

        Parameters:
            feature: Feature instance that should be returned by ``first()``.

        Returns:
            None.
        """

        self.feature = feature

    def first(self) -> StubFeature | None:
        """Return the configured feature instance.

        Returns:
            StubFeature | None: Configured feature result.
        """

        return self.feature


class StubFeatureManager:
    """Provide the filter() API expected by the migration helper."""

    def __init__(self, feature: StubFeature | None) -> None:
        """Initialize the manager stub.

        Parameters:
            feature: Feature instance returned by ``filter().first()``.

        Returns:
            None.
        """

        self.feature = feature
        self.last_filter_kwargs: dict[str, object] | None = None

    def filter(self, **kwargs) -> StubFeatureQuerySet:
        """Record the filter kwargs and return the configured query set stub.

        Parameters:
            **kwargs: Filter arguments supplied by the migration helper.

        Returns:
            StubFeatureQuerySet: Query set stub wrapping the configured feature.
        """

        self.last_filter_kwargs = kwargs
        return StubFeatureQuerySet(self.feature)


class StubApps:
    """Expose a minimal apps registry with the migration's feature model stub."""

    def __init__(self, feature: StubFeature | None) -> None:
        """Create the apps registry stub.

        Parameters:
            feature: Feature instance returned by the stub feature manager.

        Returns:
            None.
        """

        self.feature_manager = StubFeatureManager(feature)
        self.feature_model = type("FeatureModel", (), {"objects": self.feature_manager})

    def get_model(self, app_label: str, model_name: str):
        """Return the stub feature model for the requested app/model names.

        Parameters:
            app_label: Django app label requested by the migration.
            model_name: Django model name requested by the migration.

        Returns:
            type: Stub feature model.

        Raises:
            LookupError: If a different app/model is requested.
        """

        if (app_label, model_name) != ("features", "Feature"):
            raise LookupError(f"Unexpected model request: {(app_label, model_name)!r}")
        return self.feature_model


def test_0052_archive_and_restore_preserve_custom_timeout() -> None:
    """Regression: migration 0052 should round-trip custom timeout_seconds values."""

    feature = StubFeature(
        metadata={
            "parameters": {
                "model_command": "python summary.py",
                "timeout_seconds": "17",
            }
        }
    )
    apps = StubApps(feature)

    migration_0052.archive_llm_summary_model_command(apps, schema_editor=None)

    assert apps.feature_manager.last_filter_kwargs == {
        "slug": migration_0052.FEATURE_SLUG
    }
    assert feature.metadata == {
        "parameters": {"backend": "deterministic"},
        "legacy_model_command_audit": "python summary.py",
        "legacy_timeout_seconds_audit": "17",
    }
    assert feature.saved_update_fields == ["metadata", "updated_at"]

    migration_0052.restore_llm_summary_model_command(apps, schema_editor=None)

    assert feature.metadata == {
        "parameters": {
            "timeout_seconds": "17",
            "model_command": "python summary.py",
        }
    }
