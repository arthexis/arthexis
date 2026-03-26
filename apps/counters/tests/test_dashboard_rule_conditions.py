from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from apps.counters.condition_structured import parse_legacy_condition
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


class ParseLegacyConditionTests(TestCase):
    def test_parse_legacy_condition_supports_simple_boolean_expression(self):
        structured, error = parse_legacy_condition("[foo][bar] = 1")

        self.assertIsNone(error)
        self.assertIsNotNone(structured)
        self.assertEqual(structured.source, "[foo][bar]")
        self.assertEqual(structured.operator, "=")
        self.assertTrue(structured.expected_boolean)

    def test_parse_legacy_condition_marks_unsupported_expression(self):
        structured, error = parse_legacy_condition("1 = 1 AND 2 = 2")

        self.assertIsNone(structured)
        self.assertEqual(error, "Unsupported condition literal.")
