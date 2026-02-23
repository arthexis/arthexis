"""Tests for operations pending calculation and validation behavior."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.ops.models import OperationExecution, OperationScreen, pending_operations_for_user


class PendingOperationsTests(TestCase):
    """Verify pending operations are filtered by completion and recurrence windows."""

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="ops-user", password="x")

    def test_pending_operations_excludes_recently_completed(self):
        operation = OperationScreen.objects.create(
            title="Check backups",
            slug="check-backups",
            description="Ensure nightly backups pass.",
            start_url="/admin/",
            recurrence_days=7,
            is_active=True,
        )
        OperationExecution.objects.create(
            operation=operation,
            user=self.user,
            performed_at=timezone.now() - timedelta(days=2),
        )

        pending = pending_operations_for_user(self.user)

        self.assertEqual(pending, [])

    def test_pending_operations_includes_expired_completion(self):
        operation = OperationScreen.objects.create(
            title="Rotate secrets",
            slug="rotate-secrets",
            description="Rotate service credentials.",
            start_url="/admin/",
            recurrence_days=3,
            is_active=True,
        )
        OperationExecution.objects.create(
            operation=operation,
            user=self.user,
            performed_at=timezone.now() - timedelta(days=4),
        )

        pending = pending_operations_for_user(self.user)

        self.assertEqual([item.operation.id for item in pending], [operation.id])
