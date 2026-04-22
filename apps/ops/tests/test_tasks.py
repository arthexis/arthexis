"""Tests for operations recurrence notification task."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.utils import timezone

from apps.ops.models import OperationExecution, OperationScreen
from apps.ops.tasks import notify_expired_operations


class NotifyExpiredOperationsTests(TestCase):
    """Ensure users are notified only for expired, previously completed operations."""

    def test_notifies_for_expired_completed_operation(self):
        user = get_user_model().objects.create_user(
            username="ops-notify",
            email="ops@example.com",
            password="x",
        )
        operation = OperationScreen.objects.create(
            title="Verify failover",
            slug="verify-failover",
            description="Validate standby takeover.",
            start_url="/admin/",
            recurrence_days=1,
            is_active=True,
        )
        OperationExecution.objects.create(
            operation=operation,
            user=user,
            performed_at=timezone.now() - timedelta(days=2),
        )

        count = notify_expired_operations()

        self.assertEqual(count, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Operation expired", mail.outbox[0].subject)

    def test_does_not_notify_for_never_performed_operation(self):
        OperationScreen.objects.create(
            title="Review alerts",
            slug="review-alerts",
            description="Review pager alerts.",
            start_url="/admin/",
            recurrence_days=1,
            is_active=True,
        )

        count = notify_expired_operations()

        self.assertEqual(count, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_uses_latest_execution_per_user_for_expiry(self):
        user = get_user_model().objects.create_user(
            username="ops-notify-latest",
            email="latest@example.com",
            password="x",
        )
        operation = OperationScreen.objects.create(
            title="Latest only",
            slug="latest-only",
            description="Use latest execution.",
            start_url="/admin/",
            recurrence_days=3,
            is_active=True,
        )
        OperationExecution.objects.create(
            operation=operation,
            user=user,
            performed_at=timezone.now() - timedelta(days=8),
        )
        OperationExecution.objects.create(
            operation=operation,
            user=user,
            performed_at=timezone.now() - timedelta(days=1),
        )

        count = notify_expired_operations()

        self.assertEqual(count, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_skips_recently_notified_execution(self):
        user = get_user_model().objects.create_user(
            username="ops-notify-recent",
            email="recent@example.com",
            password="x",
        )
        operation = OperationScreen.objects.create(
            title="Recently notified",
            slug="recently-notified",
            description="Avoid duplicate email.",
            start_url="/admin/",
            recurrence_days=1,
            is_active=True,
        )
        OperationExecution.objects.create(
            operation=operation,
            user=user,
            performed_at=timezone.now() - timedelta(days=2),
            expiration_notified_at=timezone.now() - timedelta(hours=12),
        )

        count = notify_expired_operations()

        self.assertEqual(count, 0)
        self.assertEqual(len(mail.outbox), 0)


    def test_is_idempotent(self):
        user = get_user_model().objects.create_user(
            username="ops-notify-idempotent",
            email="idempotent@example.com",
            password="x",
        )
        operation = OperationScreen.objects.create(
            title="Idempotent",
            slug="idempotent",
            description="Send once.",
            start_url="/admin/",
            recurrence_days=1,
            is_active=True,
        )
        OperationExecution.objects.create(
            operation=operation,
            user=user,
            performed_at=timezone.now() - timedelta(days=2),
        )

        first = notify_expired_operations()
        second = notify_expired_operations()

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(len(mail.outbox), 1)

