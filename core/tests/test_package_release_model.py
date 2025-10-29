from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

import django
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

try:  # Use the pytest-specific setup when available for database readiness
    from tests.conftest import safe_setup as _safe_setup  # type: ignore
except Exception:  # pragma: no cover - fallback for direct execution
    _safe_setup = None

if _safe_setup is not None:
    _safe_setup()
else:  # pragma: no cover - fallback when pytest fixtures are unavailable
    django.setup()

from core.models import Package, PackageRelease
from core.user_data import dump_user_fixture


class PackageReleaseMigrationTests(SimpleTestCase):
    def test_version_bits_round_trip(self) -> None:
        test_cases = [
            "3.1.0",
            "5.0.1",
            "2.3.4",
        ]

        for version in test_cases:
            with self.subTest(version=version):
                release = PackageRelease(version=version)

                major, minor, patch = (int(part) for part in version.split("."))
                expected_migration = (
                    (major << PackageRelease._MAJOR_SHIFT)
                    | (minor << PackageRelease._MINOR_SHIFT)
                    | patch
                )

                self.assertEqual(release.migration_number, expected_migration)
                self.assertEqual(
                    PackageRelease.version_from_migration(expected_migration),
                    version,
                )


class PackageReleaseLatestTests(TestCase):
    def test_latest_prefers_active_package(self) -> None:
        inactive_package = Package.objects.create(name="inactive", is_active=False)
        active_package = Package.objects.create(name="active", is_active=True)

        PackageRelease.objects.create(package=inactive_package, version="9.9.9")
        active_release = PackageRelease.objects.create(
            package=active_package,
            version="1.0.0",
        )

        self.assertEqual(PackageRelease.latest(), active_release)


class PackageReleaseUserDataTests(TestCase):
    def test_delete_removes_user_data_fixture(self) -> None:
        User = get_user_model()
        user = User.objects.create_superuser(
            username="releaseuser", email="release@example.com", password="pw"
        )

        with TemporaryDirectory() as data_dir:
            user.data_path = data_dir
            user.save(update_fields=["data_path"])

            package = Package.objects.create(name="fixture-pkg")
            release = PackageRelease.objects.create(
                package=package,
                version="3.4.5",
            )

            dump_user_fixture(release, user)

            fixture_path = Path(data_dir) / user.username / (
                f"core_packagerelease_{release.pk}.json"
            )
            self.assertTrue(fixture_path.exists())

            release.delete()

            self.assertFalse(fixture_path.exists())
