from __future__ import annotations

import os

import django
from django.test import SimpleTestCase

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from core.models import Package, PackageRelease
from core.release import DEFAULT_PACKAGE


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


class PackageModelTests(SimpleTestCase):
    def test_to_package_includes_configured_modules(self) -> None:
        package = Package(packages=["gway", "projects"])

        release_package = package.to_package()

        self.assertEqual(release_package.packages, ("gway", "projects"))

    def test_to_package_falls_back_to_default_modules(self) -> None:
        package = Package(packages=[])

        release_package = package.to_package()

        self.assertEqual(release_package.packages, DEFAULT_PACKAGE.packages)
