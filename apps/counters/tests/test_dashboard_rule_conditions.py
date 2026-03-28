from decimal import Decimal
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from apps.counters.models import DashboardRule
from apps.nodes.models import Node


class DashboardRuleStructuredConditionTests(TestCase):
    def setUp(self):
        self.content_type = ContentType.objects.get_for_model(Node, for_concrete_model=False)

    def test_condition_rule_evaluates_structured_numeric_threshold(self):
        rule = DashboardRule.objects.create(
            name="Structured numeric condition",
            content_type=self.content_type,
            implementation=DashboardRule.Implementation.CONDITION,
            condition_source="7",
            condition_operator=DashboardRule.ConditionOperator.GREATER_THAN,
            condition_expected_number=Decimal("5"),
            success_message="All rules met.",
        )

        result = rule.evaluate()

        self.assertTrue(result["success"])

    def test_condition_rule_fails_when_manual_triage_is_required(self):
        rule = DashboardRule.objects.create(
            name="Needs triage",
            content_type=self.content_type,
            implementation=DashboardRule.Implementation.CONDITION,
            condition_source="",
            condition_requires_triage=True,
            condition_triage_note="Unsupported expression format.",
        )

        result = rule.evaluate()

        self.assertFalse(result["success"])
        self.assertIn("manual triage", result["message"].lower())
        self.assertIn("Unsupported expression format.", result["message"])

    def test_condition_rule_resolves_sigils_in_structured_source(self):
        rule = DashboardRule.objects.create(
            name="Structured sigil source",
            content_type=self.content_type,
            implementation=DashboardRule.Implementation.CONDITION,
            condition_source="[ENV.THRESHOLD]",
            condition_operator=DashboardRule.ConditionOperator.GREATER_THAN,
            condition_expected_number=Decimal("5"),
        )

        with patch(
            "apps.sigils.sigil_resolver.resolve_sigils", return_value="7"
        ) as resolve_sigils_mock:
            result = rule.evaluate()

        self.assertTrue(result["success"])
        resolve_sigils_mock.assert_called_once_with("[ENV.THRESHOLD]", current=rule)
