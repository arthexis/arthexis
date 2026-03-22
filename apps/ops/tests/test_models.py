"""Model tests for operations app."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.ops.models import OperationExecution, OperationScreen


class OperationScreenValidationSqlTests(TestCase):
    """Ensure validation SQL does not execute arbitrary database statements."""

    def test_run_validation_sql_rejects_custom_sql(self):
        operation = OperationScreen.objects.create(
            title="Validation disabled",
            slug="validation-disabled",
            description="Validation SQL should not run.",
            start_url="/admin/",
            validation_sql="SELECT 1",
        )

        passed, output = operation.run_validation_sql()

        self.assertFalse(passed)
        self.assertEqual(output, "Custom SQL validation is disabled for security reasons.")

    def test_save_sets_validation_status_without_executing_sql(self):
        user = get_user_model().objects.create_user(
            username="ops-model-validation",
            email="model@example.com",
            password="x",
        )
        operation = OperationScreen.objects.create(
            title="No execution",
            slug="no-execution",
            description="Validation SQL should be blocked on save.",
            start_url="/admin/",
            validation_sql="SELECT 1",
        )

        execution = OperationExecution.objects.create(operation=operation, user=user)

        self.assertFalse(execution.validation_passed)
        self.assertEqual(
            execution.validation_output,
            "Custom SQL validation is disabled for security reasons.",
        )
