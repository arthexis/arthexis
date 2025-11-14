from __future__ import annotations

import os

import django
from django.db import DatabaseError
from django.test import TestCase

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

try:  # Use the pytest-specific setup when available for database readiness
    from tests.conftest import safe_setup as _safe_setup  # type: ignore
except Exception:  # pragma: no cover - fallback for direct execution
    _safe_setup = None

if _safe_setup is not None:
    _safe_setup()
else:  # pragma: no cover - fallback when pytest fixtures are unavailable
    django.setup()

from core import tasks
from core.models import Package, PackageRelease


class ResolveReleaseSeverityTests(TestCase):
    """Database-backed tests for the ``_resolve_release_severity`` helper."""

    def test_prefers_active_release(self) -> None:
        """The helper should return the severity from an active package release."""

        version = "1.2.3"
        inactive_package = Package.objects.create(
            name="inactive-package", is_active=False
        )
        active_package = Package.objects.create(name="active-package", is_active=True)

        PackageRelease.objects.create(
            package=inactive_package,
            version=version,
            severity=PackageRelease.Severity.LOW,
        )
        expected_release = PackageRelease.objects.create(
            package=active_package,
            version=version,
            severity=PackageRelease.Severity.CRITICAL,
        )
        # Additional release with a different version ensures unrelated entries are
        # ignored when resolving severities.
        PackageRelease.objects.create(
            package=active_package,
            version="9.9.9",
            severity=PackageRelease.Severity.NORMAL,
        )

        self.assertEqual(
            tasks._resolve_release_severity(version),
            expected_release.severity,
        )

    def test_defaults_to_normal_when_no_release_matches(self) -> None:
        """When no release matches the version, the helper should return normal."""

        package = Package.objects.create(name="normal-package", is_active=True)
        PackageRelease.objects.create(
            package=package,
            version="5.4.3",
            severity=PackageRelease.Severity.LOW,
        )

        self.assertEqual(
            tasks._resolve_release_severity("6.6.6"),
            tasks.SEVERITY_NORMAL,
        )


def test_resolve_release_severity_handles_database_error(monkeypatch) -> None:
    """Database errors should be treated as a normal severity."""

    class ExplodingManager:
        def filter(self, *args, **kwargs):
            raise DatabaseError("database unavailable")

    class ExplodingModel:
        objects = ExplodingManager()

    monkeypatch.setattr(tasks, "_get_package_release_model", lambda: ExplodingModel)

    assert tasks._resolve_release_severity("7.7.7") == tasks.SEVERITY_NORMAL
