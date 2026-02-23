"""Tests for operations dashboard compliance rule."""

from django.test import TestCase

from apps.ops.dashboard_rules import evaluate_required_operations_rule
from apps.ops.models import OperationExecution, OperationScreen


class RequiredOperationsDashboardRuleTests(TestCase):
    """Validate required operations dashboard rule outcomes."""

    def _create_user(self):
        from django.contrib.auth import get_user_model

        return get_user_model().objects.create_user(username="ops-rule-user", password="x")

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

    def test_rule_passes_when_required_operation_has_execution(self):
        operation = OperationScreen.objects.create(
            title="Check inventory",
            slug="check-inventory-complete",
            description="Verify inventory levels.",
            start_url="/admin/",
            is_required=True,
            is_active=True,
        )
        OperationExecution.objects.create(
            operation=operation,
            user=self._create_user(),
            validation_passed=True,
        )

        result = evaluate_required_operations_rule()

        self.assertTrue(result["success"])
