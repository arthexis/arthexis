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

    def test_change_page_contains_promote_action(self):
        change_url = reverse("admin:core_packagerelease_change", args=[self.release.pk])
        action_url = reverse(
            "admin:core_packagerelease_actions",
            args=[self.release.pk, "promote_release_action"],
        )
        resp = self.client.get(change_url)
        self.assertContains(resp, action_url)

    def test_promote_action_redirects(self):
        url = reverse(
            "admin:core_packagerelease_actions",
            args=[self.release.pk, "promote_release_action"],
        )
        resp = self.client.post(url)
        self.assertRedirects(
            resp, reverse("release-progress", args=[self.release.pk, "promote"])
        )

    def test_change_page_shows_status_checkboxes(self):
        change_url = reverse("admin:core_packagerelease_change", args=[self.release.pk])
        resp = self.client.get(change_url)
        self.assertContains(resp, '<input type="checkbox" disabled', count=3, html=False)
        PackageRelease.objects.filter(pk=self.release.pk).update(
            is_promoted=True, is_certified=True, is_published=True
        )
        resp = self.client.get(change_url)
        self.assertContains(
            resp,
            '<input type="checkbox" checked disabled',
            count=3,
            html=False,
        )
