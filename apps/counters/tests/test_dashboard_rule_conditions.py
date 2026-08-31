from decimal import Decimal
from unittest.mock import patch

import pytest

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from apps.counters.models import DashboardRule, clear_dashboard_rule_cache
from apps.nodes.models import Node


class DashboardRuleStructuredConditionTests(TestCase):
    def setUp(self):
        self.content_type = ContentType.objects.get_for_model(
            Node, for_concrete_model=False
        )

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

    @pytest.mark.gate_upgrade
    def test_cache_invalidation_uses_content_type_id_without_relation_lookup(self):
        rule = DashboardRule(name="Stale content type relation", content_type_id=999999)

        with patch.object(DashboardRule, "invalidate_cached_value") as invalidate_mock:
            clear_dashboard_rule_cache(DashboardRule, rule)

        invalidate_mock.assert_called_once_with(999999)
