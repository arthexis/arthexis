from pathlib import Path

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch
from utils import revision as revision_utils

from packaging.version import Version

from packaging.version import Version

from core.models import Package, PackageRelease


class PackageReleaseAdminActionsTests(TestCase):
    def setUp(self):
        self.package, _ = Package.objects.get_or_create(name="arthexis")
        self.release = PackageRelease.objects.create(
            package=self.package, version="1.0.0"
        )
        User = get_user_model()
        self.user = User.objects.create_user(
            username="staff", password="pw", is_staff=True, is_superuser=True
        )
        self.client.login(username="staff", password="pw")

    def test_change_page_contains_publish_action(self):
        change_url = reverse("admin:core_packagerelease_change", args=[self.release.pk])
        resp = self.client.get(change_url)
        self.assertContains(
            resp, 'name="_action" value="publish_release_action"'
        )

    def test_publish_action_redirects(self):
        url = reverse(
            "admin:core_packagerelease_actions",
            args=[self.release.pk, "publish_release_action"],
        )
        resp = self.client.post(url)
        self.assertRedirects(
            resp, reverse("release-progress", args=[self.release.pk, "publish"])
        )

    def test_publish_action_saves_changes_before_execution(self):
        change_url = reverse("admin:core_packagerelease_change", args=[self.release.pk])
        data = {
            "package": self.package.pk,
            "release_manager": "",
            "version": "1.0.1",
            "_action": "publish_release_action",
        }
        resp = self.client.post(change_url, data)
        self.assertRedirects(
            resp, reverse("release-progress", args=[self.release.pk, "publish"])
        )
        self.release.refresh_from_db()
        self.assertEqual(self.release.version, "1.0.1")

    def test_change_page_pypi_url_readonly(self):
        change_url = reverse("admin:core_packagerelease_change", args=[self.release.pk])
        resp = self.client.get(change_url)
        content = resp.content.decode()
        self.assertIn("PyPI URL", content)
        self.assertNotIn('name="pypi_url"', content)

    def test_change_page_is_current_readonly(self):
        change_url = reverse("admin:core_packagerelease_change", args=[self.release.pk])
        with patch("utils.revision.get_revision", return_value="rev"):
            self.release.revision = "rev"
            self.release.save(update_fields=["revision"])
            resp = self.client.get(change_url)
        content = resp.content.decode()
        self.assertIn("Is current", content)
        self.assertNotIn('name="is_current"', content)

    def test_list_page_shows_is_current(self):
        list_url = reverse("admin:core_packagerelease_changelist")
        with patch("utils.revision.get_revision", return_value="rev"):
            self.release.revision = "rev"
            self.release.save(update_fields=["revision"])
            resp = self.client.get(list_url)
        content = resp.content.decode()
        self.assertIn("Is current", content)
        self.assertIn('icon-yes.svg', content)

    def test_list_page_package_links_to_package_admin(self):
        list_url = reverse("admin:core_packagerelease_changelist")
        resp = self.client.get(list_url)
        pkg_url = reverse("admin:core_package_change", args=[self.package.pk])
        self.assertContains(resp, f'href="{pkg_url}"')

    def test_release_revision_defaults_to_repo_revision(self):
        expected = revision_utils.get_revision()
        release = PackageRelease.objects.create(
            package=self.package, version="1.2.3"
        )
        self.assertEqual(release.revision, expected)
        self.assertTrue(release.is_current)

    def test_prepare_next_release_action_creates_release(self):
        change_url = reverse("admin:core_package_change", args=[self.package.pk])
        action_url = reverse(
            "admin:core_package_actions",
            args=[self.package.pk, "prepare_next_release_action"],
        )
        resp = self.client.post(action_url)
        new_release = PackageRelease.objects.get(package=self.package, version="1.0.1")
        self.assertRedirects(
            resp, reverse("admin:core_packagerelease_change", args=[new_release.pk])
        )

    def test_prepare_next_release_uses_repo_version_when_higher(self):
        version_path = Path("VERSION")
        original_version = version_path.read_text()
        self.addCleanup(lambda: version_path.write_text(original_version))
        version_path.write_text("2.0.0")

        pkg = Package.objects.create(name="pkgrepo")
        PackageRelease.objects.create(package=pkg, version="1.5.0")

        action_url = reverse(
            "admin:core_package_actions",
            args=[pkg.pk, "prepare_next_release_action"],
        )
        resp = self.client.post(action_url)

        expected_version = Version("2.0.0")
        expected_version = f"{expected_version.major}.{expected_version.minor}.{expected_version.micro + 1}"
        new_release = PackageRelease.objects.get(
            package=pkg, version=expected_version
        )
        self.assertRedirects(
            resp, reverse("admin:core_packagerelease_change", args=[new_release.pk])
        )

    def test_prepare_next_release_skips_deleted_seed_versions(self):
        pkg = Package.objects.create(name="seedpkg")
        seed = PackageRelease.objects.create(
            package=pkg, version="0.1.2", is_seed_data=True
        )
        seed.delete()
        action_url = reverse(
            "admin:core_package_actions",
            args=[pkg.pk, "prepare_next_release_action"],
        )
        resp = self.client.post(action_url)
        new_release = PackageRelease.objects.get(
            package=pkg, version="0.1.3"
        )
        self.assertRedirects(
            resp, reverse("admin:core_packagerelease_change", args=[new_release.pk])
        )


class PackageReleaseUniquePerPackageTests(TestCase):
    def setUp(self):
        self.package1 = Package.objects.create(name="pkg1")
        self.package2 = Package.objects.create(name="pkg2")
        User = get_user_model()
        self.user = User.objects.create_user(
            username="staff2", password="pw", is_staff=True, is_superuser=True
        )
        self.client.login(username="staff2", password="pw")

    def test_prepare_allows_same_version_for_different_packages(self):
        release1 = PackageRelease.objects.create(
            package=self.package1, version="0.1.2"
        )

        url2 = reverse(
            "admin:core_package_actions",
            args=[self.package2.pk, "prepare_next_release_action"],
        )
        resp2 = self.client.post(url2)
        self.assertTrue(
            PackageRelease.objects.filter(
                package=self.package2, version=release1.version
            ).exists()
        )
        release2 = PackageRelease.objects.get(
            package=self.package2, version=release1.version
        )
        self.assertRedirects(
            resp2, reverse("admin:core_packagerelease_change", args=[release2.pk])
        )
        self.assertEqual(
            PackageRelease.objects.filter(version=release1.version).count(), 2
        )


class PackageModelTests(TestCase):
    def test_package_name_unique(self):
        Package.objects.create(name="unique")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Package.objects.create(name="unique")
