"""Tests for operations dashboard compliance rule."""

from django.test import TestCase

from apps.ops.dashboard_rules import evaluate_required_operations_rule
from apps.ops.models import OperationScreen


class RequiredOperationsDashboardRuleTests(TestCase):
    """Validate required operations dashboard rule outcomes."""

    def test_rule_fails_when_required_operations_never_completed(self):
        OperationScreen.objects.create(
            title="Check inventory",
            slug="check-inventory",
            description="Verify inventory levels.",
            start_url="/admin/",
            is_required=True,
            is_active=True,
        )

        result = evaluate_required_operations_rule()

        self.assertFalse(result["success"])

    def test_rule_passes_without_missing_required_operations(self):
        result = evaluate_required_operations_rule()

        self.assertTrue(result["success"])
