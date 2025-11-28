from django.test import TestCase

from pages.dashboard_rules import bind_rule_model, evaluate_email_profile_rules


class EmailDashboardRuleTests(TestCase):
    def test_inbox_rule_reports_only_inbox_issues(self):
        with bind_rule_model("teams.emailinbox"):
            result = evaluate_email_profile_rules()

        self.assertFalse(result["success"])
        self.assertEqual(result["message"], "Configure an Email Inbox.")
        self.assertNotIn("Outbox", result["message"])

    def test_outbox_rule_reports_only_outbox_issues(self):
        with bind_rule_model("teams.emailoutbox"):
            result = evaluate_email_profile_rules()

        self.assertFalse(result["success"])
        self.assertEqual(result["message"], "Configure an Email Outbox.")
        self.assertNotIn("Inbox", result["message"])
