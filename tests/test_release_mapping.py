from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Package, PackageRelease, PackagerProfile


class ReleaseMappingTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create(username="arthexis")
        profile = PackagerProfile.objects.create(user=user, username="arthexis")
        package = Package.objects.create(release_manager=profile)
        PackageRelease.objects.create(package=package, profile=profile, version="0.1.1")

    def test_migration_number_formula(self):
        release = PackageRelease.objects.get(version="0.1.1")
        self.assertEqual(release.migration_number, 3)
        next_version = PackageRelease.version_from_migration(
            release.migration_number + 1
        )
        self.assertEqual(next_version, "1.0.0")
