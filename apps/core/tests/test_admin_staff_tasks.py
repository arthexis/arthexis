"""Regression tests for configurable staff tasks on the admin dashboard."""

from pathlib import Path
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
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

    def test_task_panel_registry_contains_system_route(self):
        """System view should be registered in task-panel route metadata."""

        route_names = {route.name for route in TASK_PANEL_ROUTES}
        self.assertIn("system", route_names)
        self.assertIn("system-reports", route_names)

    def test_system_page_uses_task_panel_rebrand(self):
        """System settings page should render task panel terminology."""

        response = self.client.get(reverse("admin:system"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Task Panels")
        self.assertContains(response, "Save task panel preferences")

    def test_sigil_builder_breadcrumb_links_back_to_task_panels(self):
        """Sigil Builder breadcrumb trail should include Task Panels as previous view."""

        response = self.client.get(reverse("admin:sigil_builder"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Task Panels")
        self.assertContains(response, reverse("admin:system"))

    def test_admin_home_shows_features_action_button(self):
        """Admin home should expose a top action button linking to Suite Features."""

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("admin:features_feature_changelist"))
        self.assertContains(response, "Features")
        self.assertContains(response, reverse("admin:chargers-shortcut"))
        self.assertContains(response, "Chargers")
        self.assertContains(response, 'id="admin-dashboard-widgets"')

    def test_admin_home_hides_features_action_without_permission(self):
        """Admin home should hide the Features action for users lacking view permission."""

        user_model = get_user_model()
        limited_staff = user_model.objects.create_user(
            username="limitedstaff",
            email="limitedstaff@example.com",
            password="admin123",
            is_staff=True,
        )
        self.client.force_login(limited_staff)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("admin:features_feature_changelist"))
        self.assertNotContains(response, "Features")
        self.assertNotContains(response, reverse("admin:chargers-shortcut"))
        self.assertNotContains(response, "Chargers")

    def test_chargers_shortcut_redirects_to_changelist_when_any_charger_exists(self):
        """Chargers shortcut should open the list directly when at least one row exists."""

        Charger.objects.create(charger_id="CP-EXISTS")

        response = self.client.get(reverse("admin:chargers-shortcut"), follow=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("admin:ocpp_charger_changelist"))

    def test_chargers_shortcut_shows_onboarding_when_no_chargers_exist(self):
        """Chargers shortcut should render onboarding guidance when list is empty."""

        response = self.client.get(reverse("admin:chargers-shortcut"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Charge point onboarding")
        self.assertContains(response, "ws://testserver/ws/&lt;charger-id&gt;/")
        self.assertContains(response, reverse("admin:ocpp_charger_add"))

    @patch("apps.core.system.admin_views.resolve_ws_scheme", return_value="wss")
    def test_chargers_shortcut_uses_proxy_aware_ws_scheme_resolution(
        self, mocked_resolver: Mock
    ):
        """Onboarding URL should use the shared proxy-aware websocket scheme resolver."""

        response = self.client.get(reverse("admin:chargers-shortcut"))

        self.assertEqual(response.status_code, 200)
        mocked_resolver.assert_called_once_with(request=response.wsgi_request)
        self.assertContains(response, "wss://testserver/ws/&lt;charger-id&gt;/")
