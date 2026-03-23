"""Regression coverage for retired sponsors application cleanup migration."""

from __future__ import annotations

from importlib import import_module


migration = import_module("apps.app.migrations.0013_remove_retired_sponsors_application")


class StubApplicationManager:
    """Capture filters and delete calls made by the migration helper."""

    def __init__(self) -> None:
        """Initialize captured query metadata."""

        self.filtered_names: tuple[str, ...] | None = None
        self.deleted = False

    def filter(self, **kwargs):
        """Record the requested application names and return self for chaining."""

        self.filtered_names = tuple(kwargs["name__in"])
        return self

    def delete(self) -> None:
        """Record that deletion was requested."""

        self.deleted = True


class StubApplication:
    """Historical app model stub exposing the manager used by the migration."""

    objects = StubApplicationManager()


class StubApps:
    """Minimal historical app registry stub for migration helper tests."""

    def get_model(self, app_label: str, model_name: str):
        """Return the stub application model for the expected lookup."""

        assert (app_label, model_name) == ("app", "Application")
        return StubApplication


def test_remove_retired_sponsors_applications_deletes_both_historical_names() -> None:
    """Regression: cleanup should remove both label and module-form sponsors rows."""

    manager = StubApplicationManager()
    StubApplication.objects = manager

    migration.remove_retired_sponsors_applications(StubApps(), schema_editor=None)

    assert manager.filtered_names == migration.RETIRED_SPONSORS_APPLICATION_NAMES
    assert manager.deleted is True
