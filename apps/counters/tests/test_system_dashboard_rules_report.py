from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from apps.counters import dashboard_rules
from apps.counters.models import DashboardRule


class DashboardRulesReportTests(TestCase):
    def setUp(self):
        self.superuser = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="pass"
        )

        def sample_rule():
            return dashboard_rules.rule_success()

        setattr(dashboard_rules, "sample_rule", sample_rule)
        self.addCleanup(lambda: delattr(dashboard_rules, "sample_rule"))

        self.rule = DashboardRule.objects.create(
            name="User dashboard rule",
            content_type=ContentType.objects.get_for_model(
                get_user_model(), for_concrete_model=False
            ),
            implementation=DashboardRule.Implementation.PYTHON,
            function_name="sample_rule",
            failure_message="Check user dashboard rule.",
        )

    def test_dashboard_rules_report_lists_rules(self):
        self.client.force_login(self.superuser)
        url = reverse("admin:system-dashboard-rules-report")

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        entries = response.context["dashboard_rule_entries"]
        self.assertEqual(len(entries), 1)

        entry = entries[0]
        self.assertEqual(entry["rule"].pk, self.rule.pk)
        self.assertEqual(entry["status"]["message"], dashboard_rules.DEFAULT_SUCCESS_MESSAGE)
        self.assertIn(str(self.rule.pk), entry["rule_admin_url"])
        self.assertContains(response, "Dashboard Rules")
        self.assertContains(response, self.rule.name)
        self.assertContains(response, self.rule.failure_message)
