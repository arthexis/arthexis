"""Regression coverage for retired blog sigil root migrations."""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

migration_0006 = import_module("apps.sigils.migrations.0006_remove_blog_sigil_roots")


class RecordingDeleteQuerySet:
    """Capture raw deletes requested through a migration queryset."""

    def __init__(self) -> None:
        """Initialize the delete recorder.

        Returns:
            None.
        """

        self.raw_delete_alias: str | None = None

    def _raw_delete(self, alias: str) -> int:
        """Record the alias used for a raw delete.

        Parameters:
            alias: Django database alias.

        Returns:
            int: Placeholder affected-row count.
        """

        self.raw_delete_alias = alias
        return 2


class RecordingSigilManager:
    """Capture filter criteria and restore payloads for sigil cleanup migrations."""

    def __init__(self) -> None:
        """Initialize the manager recorder.

        Returns:
            None.
        """

        self.last_filter_kwargs: dict[str, object] | None = None
        self.last_update_or_create_kwargs: list[dict[str, object]] = []
        self.queryset = RecordingDeleteQuerySet()
        self.used_alias: str | None = None

    def using(self, alias: str):
        """Record the database alias used by the migration.

        Parameters:
            alias: Django database alias.

        Returns:
            RecordingSigilManager: The recorder itself.
        """

        self.used_alias = alias
        return self

    def filter(self, **kwargs):
        """Record filter criteria and return a raw-delete queryset stub.

        Parameters:
            **kwargs: Query filters supplied by the migration helper.

        Returns:
            RecordingDeleteQuerySet: Recorder for raw delete operations.
        """

        self.last_filter_kwargs = kwargs
        return self.queryset

    def update_or_create(self, **kwargs):
        """Record the restore payload for a sigil root.

        Parameters:
            **kwargs: Keyword arguments supplied to ``update_or_create``.

        Returns:
            tuple[object, bool]: Minimal placeholder result.
        """

        self.last_update_or_create_kwargs.append(kwargs)
        return object(), True


class RecordingSigilRootModel:
    """Minimal SigilRoot stub exposing the manager API used by 0006."""

    _base_manager = RecordingSigilManager()


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


def test_0006_removal_hard_deletes_retired_blog_roots() -> None:
    """Regression: 0006 should raw-delete obsolete blog sigil roots."""

    manager = RecordingSigilManager()
    RecordingSigilRootModel._base_manager = manager

    migration_0006.remove_blog_roots(
        SimpleNamespace(get_model=lambda app_label, model_name: RecordingSigilRootModel),
        schema_editor=AliasSchemaEditor("archive"),
    )

    assert manager.used_alias == "archive"
    assert manager.last_filter_kwargs == {
        "prefix__in": migration_0006.BLOG_SIGIL_ROOT_PREFIXES,
    }
    assert manager.queryset.raw_delete_alias == "archive"


def test_0006_reverse_recreates_blog_sigil_roots() -> None:
    """Regression: 0006 rollback should recreate both retired blog sigil roots."""

    manager = RecordingSigilManager()
    RecordingSigilRootModel._base_manager = manager

    migration_0006.restore_blog_roots(
        SimpleNamespace(get_model=lambda app_label, model_name: RecordingSigilRootModel),
        schema_editor=AliasSchemaEditor(),
    )

    assert manager.last_update_or_create_kwargs == [
        {
            "prefix": prefix,
            "defaults": migration_0006.BLOG_SIGIL_ROOT_DEFAULTS[prefix],
        }
        for prefix in migration_0006.BLOG_SIGIL_ROOT_PREFIXES
    ]
