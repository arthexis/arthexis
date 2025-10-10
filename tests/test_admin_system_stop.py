import os
import sys
from pathlib import Path
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, reverse


class AdminSystemViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )
        self.staff = User.objects.create_user(
            username="staff",
            email="staff@example.com",
            password="password",
            is_staff=True,
        )

    def test_system_page_displays_information(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:system"))
        self.assertContains(response, "Suite installed")
        self.assertNotContains(response, "Stop Server")
        self.assertNotContains(response, "Restart")

    def test_system_page_accessible_to_staff_without_controls(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("admin:system"))
        self.assertContains(response, "Suite installed")
        self.assertNotContains(response, "Stop Server")
        self.assertNotContains(response, "Restart")

    def test_system_command_route_removed(self):
        with self.assertRaises(NoReverseMatch):
            reverse("admin:system_command", args=["check"])

    @mock.patch("core.system._open_changelog_entries", return_value=[{"sha": "abc12345", "message": "Fix bug"}])
    def test_changelog_report_page_displays_changelog(self, mock_entries):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:system-changelog-report"))
        self.assertContains(response, "Open Changelog")
        self.assertContains(response, "abc12345")
        mock_entries.assert_called_once_with()

    @mock.patch("core.system._regenerate_changelog")
    def test_changelog_report_recalculate_triggers_regeneration(self, mock_regenerate):
        self.client.force_login(self.superuser)
        response = self.client.post(
            reverse("admin:system-changelog-report"), follow=True
        )
        self.assertRedirects(response, reverse("admin:system-changelog-report"))
        mock_regenerate.assert_called_once_with()
