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

from core.models import Todo


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

    @mock.patch(
        "core.system._latest_release_changelog",
        return_value={
            "title": "v0.1.21 (2025-10-27)",
            "entries": [{"sha": "def67890", "message": "Add feature"}],
        },
    )
    @mock.patch(
        "core.system._open_changelog_entries",
        return_value=[{"sha": "abc12345", "message": "Fix bug"}],
    )
    def test_changelog_report_page_displays_changelog(
        self, mock_open_entries, mock_latest_release
    ):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:system-changelog-report"))
        self.assertContains(response, "Open Changelog")
        self.assertContains(response, "abc12345")
        self.assertContains(response, "Last Release Changelog")
        self.assertContains(response, "def67890")
        mock_open_entries.assert_called_once_with()
        mock_latest_release.assert_called_once_with()

    @mock.patch("core.system._regenerate_changelog")
    def test_changelog_report_recalculate_triggers_regeneration(self, mock_regenerate):
        self.client.force_login(self.superuser)
        response = self.client.post(
            reverse("admin:system-changelog-report"), follow=True
        )
        self.assertRedirects(response, reverse("admin:system-changelog-report"))
        mock_regenerate.assert_called_once_with()

    def test_pending_todos_report_requires_login(self):
        response = self.client.get(reverse("admin:system-pending-todos-report"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.url)

    def test_pending_todos_report_lists_pending_items(self):
        todo = Todo.objects.create(request="Review docs")
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:system-pending-todos-report"))
        self.assertContains(response, "Pending TODOs Report")
        self.assertContains(response, todo.request)
        self.assertContains(response, "Approve selected TODOs")

    def test_pending_todos_report_updates_and_approves(self):
        todo_one = Todo.objects.create(
            request="Sync translations",
            request_details="Ensure locale files are updated.",
        )
        todo_two = Todo.objects.create(
            request="Review API docs",
            request_details="Check the OAuth section",
        )
        existing_version = todo_two.version
        url = reverse("admin:system-pending-todos-report")
        self.client.force_login(self.superuser)

        data = {
            "todos-TOTAL_FORMS": "2",
            "todos-INITIAL_FORMS": "2",
            "todos-MIN_NUM_FORMS": "0",
            "todos-MAX_NUM_FORMS": "1000",
            "todos-0-id": str(todo_one.pk),
            "todos-0-request": "Sync translation updates",
            "todos-0-request_details": "Ensure locale files include new strings.",
            "todos-0-url": "",
            "todos-0-version": "2.0.0",
            "todos-0-generated_for_version": "1.2.3",
            "todos-0-generated_for_revision": "",
            "todos-0-on_done_condition": "",
            "todos-0-mark_done": "on",
            "todos-1-id": str(todo_two.pk),
            "todos-1-request": "Review API docs",
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
