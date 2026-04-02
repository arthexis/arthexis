"""Model tests for operations app."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
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

        self.assertIsNone(passed)
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

        self.assertIsNone(execution.validation_passed)
        self.assertEqual(
            execution.validation_output,
            "Custom SQL validation is disabled for security reasons.",
        )

    def test_clean_rejects_absolute_start_url(self):
        operation = OperationScreen(
            title="Absolute URL",
            slug="absolute-url",
            description="Should fail clean validation.",
            start_url="https://example.com/admin/",
        )

        with self.assertRaises(ValidationError):
            operation.full_clean()

    def test_clean_rejects_relative_start_url_without_leading_slash(self):
        operation = OperationScreen(
            title="Missing slash",
            slug="missing-slash",
            description="Should fail clean validation.",
            start_url="admin/",
        )

        with self.assertRaises(ValidationError):
            operation.full_clean()
