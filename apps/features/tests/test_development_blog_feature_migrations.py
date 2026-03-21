"""Regression coverage for development blog feature retirement migrations."""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

migration_0018 = import_module(
    "apps.features.migrations.0018_rename_development_blog_feature"
)
migration_0048 = import_module(
    "apps.features.migrations.0048_remove_development_blog_feature"
)


class RecordingUpdateQuerySet:
    """Capture updates requested through a migration queryset."""

    def __init__(self, count: int = 0) -> None:
        """Initialize the queryset recorder.

        Parameters:
            count: Number of rows the update should report.

        Returns:
            None.
        """

        self.count = count
        self.last_update_kwargs: dict[str, object] | None = None

    def update(self, **kwargs):
        """Record the queryset update payload.

        Parameters:
            **kwargs: Update fields requested by the migration helper.

        Returns:
            int: The configured row count.
        """

        self.last_update_kwargs = kwargs
        return self.count


class RecordingUpdateManager:
    """Capture filter criteria and update-or-create calls for migration helpers."""

    def __init__(self, *, update_count: int = 0) -> None:
        """Initialize the manager recorder.

        Parameters:
            update_count: Number of rows reported by queryset updates.

        Returns:
            None.
        """

        self.last_filter_kwargs: dict[str, object] | None = None
        self.last_update_or_create_kwargs: dict[str, object] | None = None
        self.queryset = RecordingUpdateQuerySet(count=update_count)
        self.used_alias: str | None = None

    def using(self, alias: str):
        """Record the database alias used by the migration.

        Parameters:
            alias: Django database alias.

        Returns:
            RecordingUpdateManager: The recorder itself.
        """

        self.used_alias = alias
        return self

    def filter(self, **kwargs):
        """Record filter criteria and return the configured queryset.

        Parameters:
            **kwargs: Query filters supplied by the migration helper.

        Returns:
            RecordingUpdateQuerySet: Recorder for queryset updates.
        """

        self.last_filter_kwargs = kwargs
        return self.queryset

    def update_or_create(self, **kwargs):
        """Record the fallback restore payload.

        Parameters:
            **kwargs: Keyword arguments supplied to ``update_or_create``.

        Returns:
            tuple[object, bool]: Minimal placeholder result.
        """

        self.last_update_or_create_kwargs = kwargs
        return object(), True


class RecordingFeatureModel:
    """Minimal Feature stub exposing the manager API used by 0048."""

    _base_manager = RecordingUpdateManager()


class AliasSchemaEditor:
    """Minimal schema editor stub exposing the migration database alias."""

    def __init__(self, alias: str = "default") -> None:
        """Initialize the schema editor stub.

        Parameters:
            alias: Django database alias.

        Returns:
            None.
        """

        self.connection = SimpleNamespace(alias=alias)


def test_0048_soft_delete_targets_both_blog_feature_slugs() -> None:
    """Regression: 0048 should hide both historical development blog slugs."""

    manager = RecordingUpdateManager()
    RecordingFeatureModel._base_manager = manager

    migration_0048.remove_feature(
        SimpleNamespace(get_model=lambda app_label, model_name: RecordingFeatureModel),
        schema_editor=AliasSchemaEditor("archive"),
    )

    assert manager.used_alias == "archive"
    assert manager.last_filter_kwargs == {"slug__in": migration_0048.FEATURE_SLUGS}
    assert manager.queryset.last_update_kwargs == {"is_deleted": True}


def test_0048_reverse_restores_soft_deleted_rows_before_recreating() -> None:
    """Regression: 0048 rollback should undelete matching rows without recreating them."""

    manager = RecordingUpdateManager(update_count=2)
    RecordingFeatureModel._base_manager = manager

    migration_0048.restore_feature(
        SimpleNamespace(get_model=lambda app_label, model_name: RecordingFeatureModel),
        schema_editor=AliasSchemaEditor(),
    )

    assert manager.last_filter_kwargs == {"slug__in": migration_0048.FEATURE_SLUGS}
    assert manager.queryset.last_update_kwargs == {"is_deleted": False}
    assert manager.last_update_or_create_kwargs is None


def test_0048_reverse_recreates_feature_when_nothing_was_soft_deleted() -> None:
    """Regression: 0048 rollback should recreate the feature when no archived row exists."""

    manager = RecordingUpdateManager(update_count=0)
    RecordingFeatureModel._base_manager = manager

    migration_0048.restore_feature(
        SimpleNamespace(get_model=lambda app_label, model_name: RecordingFeatureModel),
        schema_editor=AliasSchemaEditor(),
    )

    assert manager.last_update_or_create_kwargs == {
        "slug": migration_0048.FEATURE_SLUG,
        "defaults": {**migration_0048.FEATURE_BACKUP, "is_deleted": False},
    }
