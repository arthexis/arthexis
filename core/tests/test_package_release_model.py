from __future__ import annotations

from django.test import TestCase

from core.models import Package, PackageRelease


class PackageReleaseMatchesRevisionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.package = Package.objects.create(name="core-test-package", is_active=True)
        cls.release = PackageRelease.objects.create(
            package=cls.package,
            version="1.2.3",
            revision="rev1",
        )

    def test_returns_true_when_revision_matches(self):
        self.assertTrue(
            PackageRelease.matches_revision(self.release.version, self.release.revision)
        )

    def test_returns_false_when_revision_does_not_match(self):
        self.assertFalse(PackageRelease.matches_revision(self.release.version, "rev2"))

    def test_returns_true_for_inactive_package(self):
        self.package.is_active = False
        self.package.save(update_fields=["is_active"])

        self.assertTrue(PackageRelease.matches_revision("1.2.3", "rev1"))

    def test_returns_true_for_empty_version_or_revision(self):
        self.assertTrue(PackageRelease.matches_revision("", ""))
        self.assertTrue(PackageRelease.matches_revision("1.2.3", ""))
        self.assertTrue(PackageRelease.matches_revision("", "rev1"))
