"""Regression tests for configurable staff tasks on the admin dashboard."""

from pathlib import Path
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from apps.actions.models import StaffTask
from apps.actions.staff_tasks import ensure_default_staff_tasks_exist
from apps.core.system.admin_views import TASK_PANEL_ROUTES
from apps.ocpp.models import Charger


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
        """Reports runner should reject direct submission for restricted report routes."""

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
        route_names = {route["name"] for route in response.context["report_routes"]}
        self.assertNotIn("system-upgrade-report", route_names)

    @patch("apps.core.system.admin_views._trigger_upgrade_check")
    def test_staff_without_upgrade_privilege_cannot_trigger_upgrade_check(
        self, mocked_trigger_upgrade_check: Mock
    ):
        """Staff users without upgrade privileges should receive a permission error."""

        user_model = get_user_model()
        staff_user = user_model.objects.create_user(
            username="staffer3",
            email="staffer3@example.com",
            password="admin123",
            is_staff=True,
        )
        self.client.force_login(staff_user)

        response = self.client.post(reverse("admin:system-upgrade-run-check"), {"channel": "stable"})

        self.assertEqual(response.status_code, 403)
        mocked_trigger_upgrade_check.assert_not_called()

    def test_staff_without_upgrade_privilege_cannot_access_upgrade_report_directly(self):
        """Staff users without upgrade privileges should receive 403 on report view."""

        user_model = get_user_model()
        staff_user = user_model.objects.create_user(
            username="staffer5",
            email="staffer5@example.com",
            password="admin123",
            is_staff=True,
        )
        self.client.force_login(staff_user)

        response = self.client.get(reverse("admin:system-upgrade-report"))

        self.assertEqual(response.status_code, 403)

    def test_staff_without_upgrade_privilege_cannot_refresh_upgrade_revision(self):
        """Staff users without upgrade privileges should receive 403 on revision refresh."""

        user_model = get_user_model()
        staff_user = user_model.objects.create_user(
            username="staffer6",
            email="staffer6@example.com",
            password="admin123",
            is_staff=True,
        )
        self.client.force_login(staff_user)

        response = self.client.post(reverse("admin:system-upgrade-check-revision"))

        self.assertEqual(response.status_code, 403)

    @patch("apps.core.system.admin_views._trigger_upgrade_check", return_value=True)
    def test_staff_with_upgrade_privilege_can_trigger_upgrade_check(self, _mocked_trigger: Mock):
        """Staff users with explicit upgrade privilege should be able to trigger checks."""

        user_model = get_user_model()
        staff_user = user_model.objects.create_user(
            username="staffer4",
            email="staffer4@example.com",
            password="admin123",
            is_staff=True,
        )
        permission = Permission.objects.get(codename="can_trigger_upgrade_checks")
        staff_user.user_permissions.add(permission)
        self.client.force_login(staff_user)

        reports_response = self.client.get(reverse("admin:system-reports"))
        route_names = {route["name"] for route in reports_response.context["report_routes"]}
        self.assertIn("system-upgrade-report", route_names)

        response = self.client.post(reverse("admin:system-upgrade-run-check"), {"channel": "stable"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin:system-upgrade-report"))

    @patch("apps.core.system.admin_views.resolve_ws_scheme", return_value="wss")
    def test_chargers_shortcut_uses_proxy_aware_ws_scheme_resolution(
        self, mocked_resolver: Mock
    ):
        """Onboarding URL should use the shared proxy-aware websocket scheme resolver."""

        response = self.client.get(reverse("admin:chargers-shortcut"))

        self.assertEqual(response.status_code, 200)
        mocked_resolver.assert_called_once_with(request=response.wsgi_request)
        self.assertContains(response, "wss://testserver/ws/&lt;charger-id&gt;/")
