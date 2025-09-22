import os
import sys
from pathlib import Path

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
