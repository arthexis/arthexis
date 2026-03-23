"""Regression coverage for the Google Drive retirement migration helpers."""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

migration = import_module(
    "apps.calendars.migrations.0003_google_account_localization_and_gdrive_retirement"
)


class _FieldStub:
    """Minimal field clone stub for exercising alter-field migration helpers."""

    def __init__(self, remote_model):
        """Store the remote model reference exposed through ``remote_field``."""

        self.remote_field = SimpleNamespace(model=remote_model)

    def clone(self):
        """Return a shallow clone that preserves the remote model reference."""

        return _FieldStub(self.remote_field.model)


class _MetaStub:
    """Expose Django-like ``_meta.get_field`` behavior for tests."""

    def __init__(self, field):
        """Store the field returned by ``get_field``."""

        self._field = field

    def get_field(self, name):
        """Return the requested field stub.

        Parameters:
            name: Field name requested by the migration helper.

        Returns:
            _FieldStub: The requested account field stub.

        Raises:
            AssertionError: If an unexpected field name is requested.
        """
        assert name == "account"
        return self._field


class _ModelStub:
    """Small model stub exposing the ``_meta`` API used by schema editors."""

    def __init__(self, field):
        """Attach the field stub to a Django-like ``_meta`` object."""

        self._meta = _MetaStub(field)


class _AppsStub:
    """Historical app-registry stub keyed by app label and model name."""

    def __init__(self):
        """Create reusable gdrive/calendars model stubs."""

        self.gdrive_google_account = object()
        self.calendars_google_account = object()
        self.google_calendar_field = _FieldStub(self.calendars_google_account)
        self.google_calendar = _ModelStub(self.google_calendar_field)

    def get_model(self, app_label, model_name):
        """Return the requested historical model stub."""

        models = {
            ("gdrive", "GoogleAccount"): self.gdrive_google_account,
            ("calendars", "GoogleAccount"): self.calendars_google_account,
            ("calendars", "GoogleCalendar"): self.google_calendar,
        }
        return models[(app_label, model_name)]


class _SchemaEditorStub:
    """Capture schema-editor operations invoked by the migration helpers."""

    def __init__(self, tables):
        """Expose visible tables and collect create/alter/delete calls."""

        self.connection = SimpleNamespace(
            introspection=SimpleNamespace(table_names=lambda: tables)
        )
        self.created_models = []
        self.deleted_models = []
        self.altered_fields = []

    def create_model(self, model):
        """Record model creation requests."""

        self.created_models.append(model)

    def delete_model(self, model):
        """Record model deletion requests."""

        self.deleted_models.append(model)

    def alter_field(self, model, old_field, new_field, strict=False):
        """Record field retargeting requests."""

        self.altered_fields.append((model, old_field, new_field, strict))


def test_create_google_account_table_uses_schema_editor_model_creation():
    """The migration should create the GoogleAccount table via schema_editor."""

    apps = _AppsStub()
    schema_editor = _SchemaEditorStub(["gdrive_googleaccount"])

    migration.create_calendars_googleaccount_table(apps, schema_editor)

    assert schema_editor.created_models == [apps.calendars_google_account]


def test_retarget_googlecalendar_account_constraint_swaps_to_calendars_model():
    """The live GoogleCalendar FK should be repointed away from gdrive."""

    apps = _AppsStub()
    schema_editor = _SchemaEditorStub(["calendars_googlecalendar"])

    migration.retarget_googlecalendar_account_constraint(apps, schema_editor)

    model, old_field, new_field, strict = schema_editor.altered_fields[0]
    assert model is apps.google_calendar
    assert old_field.remote_field.model is apps.gdrive_google_account
    assert new_field.remote_field.model is apps.calendars_google_account
    assert strict is False
