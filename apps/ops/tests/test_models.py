"""Model tests for operations app."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.ops.models import (
    VALIDATION_SQL_DISABLED_MESSAGE,
    OperationExecution,
    OperationScreen,
    pending_operations_for_user,
)


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
        self.assertEqual(output, VALIDATION_SQL_DISABLED_MESSAGE)

    def test_save_rejects_completion_when_validation_sql_is_configured(self):
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
            is_required=True,
        )

        with self.assertRaises(ValidationError) as exc:
            OperationExecution.objects.create(operation=operation, user=user)

        self.assertEqual(exc.exception.message_dict, {"validation_sql": [str(VALIDATION_SQL_DISABLED_MESSAGE)]})
        self.assertFalse(OperationExecution.objects.filter(operation=operation, user=user).exists())
        pending = pending_operations_for_user(user, required_only=True)
        self.assertEqual([item.operation.pk for item in pending], [operation.pk])
