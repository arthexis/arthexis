from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Package, PackageRelease, ReleaseManager


class ReleaseMappingTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.get(username="arthexis")
        manager = ReleaseManager.objects.get(user=user)
        package = Package.objects.get(name="arthexis")
        PackageRelease.objects.get_or_create(
            version="0.1.1", defaults={"package": package, "release_manager": manager}
        )

    def test_migration_number_formula(self):
        release = PackageRelease.objects.get(version="0.1.1")
        self.assertEqual(release.migration_number, 3)
        next_version = PackageRelease.version_from_migration(
            release.migration_number + 1
        )
        self.assertEqual(next_version, "1.0.0")
