import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from core.models import ReleaseManager


class AdminProfileLinkTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="profileadmin",
            email="profileadmin@example.com",
            password="password",
        )
        self.client.force_login(self.user)

    def test_profile_link_points_to_user_admin(self):
        response = self.client.get(reverse("admin:index"))
        expected_url = reverse("admin:teams_user_change", args=[self.user.pk])
        self.assertContains(response, "Active Profile")
        self.assertContains(response, f'href="{expected_url}"')

    def test_profile_link_shows_unset_when_missing(self):
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "Active Profile (Unset)")

    def test_profile_link_shows_profile_name_when_available(self):
        profile = ReleaseManager.objects.create(user=self.user)
        response = self.client.get(reverse("admin:index"))
        expected_url = reverse("admin:teams_releasemanager_change", args=[profile.pk])
        expected_label = f"Active Profile ({profile})"
        self.assertContains(response, expected_label)
        self.assertContains(response, f'href="{expected_url}"')
