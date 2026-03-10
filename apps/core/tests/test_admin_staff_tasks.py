"""Regression tests for configurable staff tasks on the admin dashboard."""

from pathlib import Path
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.actions.models import StaffTask
from apps.actions.staff_tasks import ensure_default_staff_tasks_exist


class AdminStaffTasksTests(TestCase):
    """Validate dashboard task buttons and user toggles."""

    def setUp(self):
        ensure_default_staff_tasks_exist()
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="adminstaff",
            email="adminstaff@example.com",
            password="admin123",
        )
        self.client.force_login(self.user)
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

    def test_reports_runner_rejects_superuser_only_report_selection_for_staff_user(self):
        """Reports runner should reject direct submission for superuser-only report routes."""

        user_model = get_user_model()
        staff_user = user_model.objects.create_user(
            username="staffer2",
            email="staffer2@example.com",
            password="admin123",
            is_staff=True,
        )
        self.client.force_login(staff_user)

        response = self.client.post(
            reverse("admin:system-reports"),
            {"report": "system-upgrade-report", "params": ""},
            follow=False,
        )

        self.assertEqual(response.status_code, 200)
        messages = list(response.context["messages"])
        self.assertTrue(any("do not have access" in str(message) for message in messages))
