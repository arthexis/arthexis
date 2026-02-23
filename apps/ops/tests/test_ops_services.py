"""Regression tests for operations pending calculations and reminders."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.ops.models import OperationExecution, OperationReminder, OperationScreen
from apps.ops.services import pending_operations_for_user
from apps.ops.tasks import notify_expired_operations


class PendingOperationsTests(TestCase):
    """Validate operation pending logic for user-scoped operations."""

    def setUp(self) -> None:
        User = get_user_model()
        self.user = User.objects.create_user(
            username="ops-user",
            password="password123",
            email="ops-user@example.com",
            is_staff=True,
        )

    def test_operation_is_pending_until_completed(self) -> None:
        """A brand new operation should appear in the pending list."""

        operation = OperationScreen.objects.create(
            title="Validate backups",
            slug="validate-backups",
            description="Run backup validation script.",
            start_url="/admin/",
            priority=1,
        )

        pending = list(pending_operations_for_user(self.user))
        self.assertEqual([operation.pk], [item.pk for item in pending])

    def test_expired_completion_becomes_pending_again(self) -> None:
        """Completed operations should reappear after expiration window passes."""

        operation = OperationScreen.objects.create(
            title="Rotate credentials",
            slug="rotate-credentials",
            description="Rotate API credentials.",
            start_url="/admin/",
            expires_after_days=7,
        )
        OperationExecution.objects.create(
            operation=operation,
            user=self.user,
            completed_at=timezone.now() - timedelta(days=8),
        )

        pending = list(pending_operations_for_user(self.user))
        self.assertEqual([operation.pk], [item.pk for item in pending])


class NotifyExpiredOperationsTests(TestCase):
    """Ensure expired reminders are emitted only for completed expired operations."""

    def setUp(self) -> None:
        User = get_user_model()
        self.user = User.objects.create_user(
            username="ops-reminder",
            password="password123",
            email="ops-reminder@example.com",
            is_staff=True,
        )

    def test_notify_expired_operations_records_single_reminder(self) -> None:
        """Task should log exactly one reminder entry per expired completion."""

        operation = OperationScreen.objects.create(
            title="Review logs",
            slug="review-logs",
            description="Review error logs.",
            start_url="/admin/",
            expires_after_days=1,
        )
        execution = OperationExecution.objects.create(
            operation=operation,
            user=self.user,
            completed_at=timezone.now() - timedelta(days=2),
        )

        sent = notify_expired_operations()
        self.assertEqual(1, sent)
        self.assertTrue(OperationReminder.objects.filter(execution=execution).exists())

        sent_again = notify_expired_operations()
        self.assertEqual(0, sent_again)
