from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from apps.counters import dashboard_rules
from apps.counters.models import DashboardRule


class DashboardBadgeTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.superuser = self.user_model.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(self.superuser)

    def test_dashboard_rows_include_model_badges_when_rules_exist(self):
        def sample_rule():
            return dashboard_rules.rule_failure("Sample failure")

        setattr(dashboard_rules, "sample_rule", sample_rule)
        self.addCleanup(lambda: delattr(dashboard_rules, "sample_rule"))

        DashboardRule.objects.create(
            name="User dashboard rule",
            content_type=ContentType.objects.get_for_model(
                self.user_model, for_concrete_model=False
            ),
            implementation=DashboardRule.Implementation.PYTHON,
            function_name="sample_rule",
        )

        response = self.client.get(reverse("admin:index"))

        self.assertContains(response, "dashboard-model-status")
        self.assertContains(response, "Sample failure")

    def test_dashboard_model_status_endpoint_is_unavailable(self):
        with self.assertRaises(NoReverseMatch):
            reverse("admin:dashboard_model_status")
