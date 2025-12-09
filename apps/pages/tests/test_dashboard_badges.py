from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from apps.counters import dashboard_rules
from apps.counters.dashboard_rules import rule_failure, rule_success
from apps.counters.models import DashboardRule


def passing_rule():
    return rule_success("Everything is fine.")


def failing_rule():
    return rule_failure("Everything is broken.")


class DashboardBadgeTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.superuser = self.user_model.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(self.superuser)
        self.content_type = ContentType.objects.get_for_model(self.user_model)

    def test_dashboard_rows_include_htmx_loader(self):
        response = self.client.get(reverse("admin:index"))

        self.assertContains(response, "data-model-status-loader")
        self.assertContains(response, 'hx-trigger="revealed"')
        self.assertContains(
            response,
            reverse("admin:dashboard_model_status"),
        )

    def test_dashboard_status_view_renders_rule_status(self):
        dashboard_rules.passing_rule = passing_rule
        self.addCleanup(lambda: setattr(dashboard_rules, "passing_rule", passing_rule))

        DashboardRule.objects.create(
            content_type=self.content_type,
            name="users:passing",
            function_name="passing_rule",
        )

        response = self.client.get(
            reverse("admin:dashboard_model_status"),
            {"app": self.content_type.app_label, "model": self.user_model.__name__},
        )

        self.assertContains(response, "model-rule-status")
        self.assertContains(response, "✓")
        self.assertContains(response, "Everything is fine.")

    def test_dashboard_status_view_rejects_invalid_methods(self):
        response = self.client.post(
            reverse("admin:dashboard_model_status"),
            {"app": self.content_type.app_label, "model": self.user_model.__name__},
        )

        self.assertEqual(response.status_code, 405)
        self.assertIn("GET", response.headers.get("Allow", ""))

    def test_dashboard_status_requires_valid_model_and_permissions(self):
        response = self.client.get(reverse("admin:dashboard_model_status"))
        self.assertEqual(response.status_code, 400)

        limited_user = self.user_model.objects.create_user(
            username="limited",
            email="limited@example.com",
            password="password",
            is_staff=True,
        )
        self.client.force_login(limited_user)

        response = self.client.get(
            reverse("admin:dashboard_model_status"),
            {"app": self.content_type.app_label, "model": "unknown"},
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.get(
            reverse("admin:dashboard_model_status"),
            {"app": self.content_type.app_label, "model": self.user_model.__name__},
        )
        self.assertEqual(response.status_code, 403)
