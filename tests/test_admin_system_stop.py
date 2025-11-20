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

            "todos-1-request_details": "Check the OAuth section and callbacks.",
            "todos-1-url": "/docs/api/",
            "todos-1-version": existing_version,
            "todos-1-generated_for_version": "",
            "todos-1-generated_for_revision": "rev-2",
            "todos-1-on_done_condition": "",
        }

        response = self.client.post(url, data, follow=True)
        self.assertRedirects(response, url)

        todo_one.refresh_from_db()
        todo_two.refresh_from_db()
        self.assertIsNotNone(todo_one.done_on)
        self.assertEqual(todo_one.request, "Sync translation updates")
        self.assertEqual(
            todo_one.request_details,
            "Ensure locale files include new strings.",
        )
        self.assertEqual(todo_one.generated_for_version, "1.2.3")
        self.assertEqual(todo_one.version, "2.0.0")
        self.assertEqual(todo_two.generated_for_revision, "rev-2")
        self.assertEqual(todo_two.version, existing_version)
        self.assertIsNone(todo_two.done_on)
