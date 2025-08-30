from django.test import TestCase
from core.models import Package, PackageRelease


class ReleaseMappingTests(TestCase):
    def setUp(self):
        self.package = Package.objects.create()
        self.release = PackageRelease.objects.create(
            package=self.package, version="0.1.1", release="abc"
        )

    def test_migration_number_formula(self):
        release = self.release
        self.assertEqual(release.migration_number, 3)
        next_version = PackageRelease.version_from_migration(
            release.migration_number + 1
        )
        self.assertEqual(next_version, "1.0.0")
