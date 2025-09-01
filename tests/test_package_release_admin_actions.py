from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

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
        action_url = reverse(
            "admin:core_packagerelease_actions",
            args=[self.release.pk, "publish_release_action"],
        )
        resp = self.client.get(change_url)
        self.assertContains(resp, action_url)

    def test_publish_action_redirects(self):
        url = reverse(
            "admin:core_packagerelease_actions",
            args=[self.release.pk, "publish_release_action"],
        )
        resp = self.client.post(url)
        self.assertRedirects(
            resp, reverse("release-progress", args=[self.release.pk, "publish"])
        )

    def test_change_page_pypi_url_readonly(self):
        change_url = reverse("admin:core_packagerelease_change", args=[self.release.pk])
        resp = self.client.get(change_url)
        content = resp.content.decode()
        self.assertNotIn('name="pypi_url"', content)

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
