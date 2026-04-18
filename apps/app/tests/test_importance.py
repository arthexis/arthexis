"""Tests for application importance choices."""

import importlib

from django.apps import apps as django_apps

from apps.app.models import Application

mark_legacy_applications = importlib.import_module(
    "apps.app.migrations.0002_application_importance_legacy"
).mark_legacy_applications


def test_application_importance_includes_legacy_choice() -> None:
    assert Application.Importance.LEGACY == "legacy"
    assert ("legacy", "Legacy") in Application.Importance.choices


class _SchemaEditorStub:
    def __init__(self, alias: str):
        self.connection = type("Connection", (), {"alias": alias})()


def test_mark_legacy_applications_updates_baseline_only(db) -> None:
    legacy_baseline = Application.objects.create(
        name="legacy core",
        importance=Application.Importance.BASELINE,
    )
    legacy_critical = Application.objects.create(
        name="legacy critical",
        importance=Application.Importance.CRITICAL,
    )
    non_legacy_baseline = Application.objects.create(
        name="core app",
        importance=Application.Importance.BASELINE,
    )

    mark_legacy_applications(
        django_apps,
        _SchemaEditorStub(alias=legacy_baseline._state.db),
    )

    legacy_baseline.refresh_from_db()
    legacy_critical.refresh_from_db()
    non_legacy_baseline.refresh_from_db()

    assert legacy_baseline.importance == Application.Importance.LEGACY
    assert legacy_critical.importance == Application.Importance.CRITICAL
    assert non_legacy_baseline.importance == Application.Importance.BASELINE
