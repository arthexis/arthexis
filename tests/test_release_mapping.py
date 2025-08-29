from django.test import TestCase
from core.models import PackageRelease


class ReleaseMappingTests(TestCase):
    fixtures = ["package_releases.json"]

    def test_migration_number_formula(self):
        release = PackageRelease.objects.get(version="0.1.1")
        self.assertEqual(release.migration_number, 3)
        next_version = PackageRelease.version_from_migration(release.migration_number + 1)
        self.assertEqual(next_version, "1.0.0")
