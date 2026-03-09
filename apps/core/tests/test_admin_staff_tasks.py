"""Regression tests for configurable staff tasks on the admin dashboard."""

from pathlib import Path
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.actions.models import StaffTask


class AdminStaffTasksTests(TestCase):
    """Validate dashboard task buttons and user toggles."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="adminstaff",
            email="adminstaff@example.com",
            password="admin123",
        )
        self.client.force_login(self.user)

    def test_admin_dashboard_uses_tasks_button_labels(self):
        """Dashboard should surface task buttons from configurable staff task records."""

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(">Tasks<", content)
        self.assertIn(">Rules<", content)
        self.assertIn(">System<", content)
        self.assertIn(">Reports<", content)
        self.assertIn("admin-home-net-message__icon", content)
        self.assertNotIn(">Net message<", content)

    def test_staff_member_can_toggle_dashboard_task_visibility(self):
        """Staff users can hide a dashboard task from their own top-button row."""

        system_url = reverse("admin:system")
        rules_task = StaffTask.objects.get(slug="rules")
        task_ids = [str(task.pk) for task in StaffTask.objects.exclude(pk=rules_task.pk)]

        response = self.client.post(system_url, {"dashboard_tasks": task_ids}, follow=True)

        self.assertEqual(response.status_code, 200)
        dashboard_response = self.client.get(reverse("admin:index"))
        dashboard_html = dashboard_response.content.decode()
        self.assertNotIn(">Rules<", dashboard_html)
        self.assertIn(">Tasks<", dashboard_html)

    @patch("apps.core.system.admin_views._systemctl_command", return_value=["systemctl"])
    @patch("apps.core.system.admin_views.subprocess.run")
    def test_system_view_shows_restart_button_when_superuser_and_service_active(
        self, mocked_run: Mock, _mocked_command: Mock
    ):
        """System view should expose restart action only for active service superusers."""

        mocked_run.return_value.returncode = 0
        lock_dir = Path(settings.BASE_DIR) / ".locks"
        lock_dir.mkdir(exist_ok=True)
        (lock_dir / "service.lck").write_text("suite", encoding="utf-8")

        response = self.client.get(reverse("admin:system-details"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Restart Server")

    @patch("apps.core.system.admin_views._systemctl_command", return_value=["systemctl"])
    @patch("apps.core.system.admin_views.subprocess.run")
    def test_system_restart_endpoint_restarts_service_for_active_superuser(
        self, mocked_run: Mock, _mocked_command: Mock
    ):
        """Restart endpoint should invoke systemctl restart for active service superusers."""

        mocked_run.side_effect = [Mock(returncode=0), Mock(returncode=0)]
        lock_dir = Path(settings.BASE_DIR) / ".locks"
        lock_dir.mkdir(exist_ok=True)
        (lock_dir / "service.lck").write_text("suite", encoding="utf-8")

        response = self.client.post(reverse("admin:system-restart-server"), follow=False)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(any("restart" in " ".join(call.args[0]) for call in mocked_run.call_args_list))


    def test_reports_runner_lists_known_report_routes(self):
        """Reports runner should include existing report views that can be launched."""

        response = self.client.get(reverse("admin:system-reports"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "System Startup Report")
        self.assertContains(response, "System Sql Report")

    def test_reports_runner_redirects_to_selected_report_with_params(self):
        """Reports runner should redirect to selected report route with query parameters."""

        response = self.client.post(
            reverse("admin:system-reports"),
            {"report": "system-startup-report", "params": "limit=25"},
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{reverse('admin:system-startup-report')}?limit=25")

    def test_reports_runner_does_not_double_encode_query_values(self):
        """Reports runner should normalize encoded query values without double-encoding."""

        response = self.client.post(
            reverse("admin:system-reports"),
            {"report": "system-startup-report", "params": "q=hello%20world"},
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{reverse('admin:system-startup-report')}?q=hello+world")
