from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.maps.models import Location
from apps.tasks.models import ManualTaskReport, ManualTaskRequest


class ManualTaskRequestModelTests(TestCase):
    def test_requires_target(self):
        request = ManualTaskRequest(
            description="Inspect site",
            scheduled_start=timezone.now(),
            scheduled_end=timezone.now() + timedelta(hours=1),
            is_periodic=False,
        )

        with self.assertRaises(ValidationError) as exc:
            request.full_clean()

        self.assertIn("node", exc.exception.message_dict)
        self.assertIn("location", exc.exception.message_dict)

    def test_schedule_order(self):
        location = Location.objects.create(name="Test Yard")
        request = ManualTaskRequest(
            description="Replace filter",
            location=location,
            scheduled_start=timezone.now(),
            scheduled_end=timezone.now() - timedelta(hours=1),
            is_periodic=False,
        )

        with self.assertRaises(ValidationError) as exc:
            request.full_clean()

        self.assertIn("scheduled_end", exc.exception.message_dict)

    def test_periodic_requires_period(self):
        location = Location.objects.create(name="Test Yard")
        request = ManualTaskRequest(
            description="Audit safety equipment",
            location=location,
            scheduled_start=timezone.now(),
            scheduled_end=timezone.now() + timedelta(hours=2),
            is_periodic=True,
            period_deadline=timedelta(days=2),
        )

        with self.assertRaises(ValidationError) as exc:
            request.full_clean()

        self.assertIn("period", exc.exception.message_dict)

    def test_period_deadline_must_be_within_period(self):
        location = Location.objects.create(name="Test Yard")
        request = ManualTaskRequest(
            description="Audit safety equipment",
            location=location,
            scheduled_start=timezone.now(),
            scheduled_end=timezone.now() + timedelta(hours=2),
            is_periodic=True,
            period=timedelta(days=1),
            period_deadline=timedelta(days=2),
        )

        with self.assertRaises(ValidationError) as exc:
            request.full_clean()

        self.assertIn("period_deadline", exc.exception.message_dict)


class ManualTaskReportModelTests(TestCase):
    def test_string_representation(self):
        location = Location.objects.create(name="Test Yard")
        task_request = ManualTaskRequest.objects.create(
            description="Inspect panels",
            location=location,
            scheduled_start=timezone.now(),
            scheduled_end=timezone.now() + timedelta(hours=1),
        )
        report = ManualTaskReport.objects.create(
            request=task_request,
            details="Completed inspection",
        )

        self.assertIn(task_request.description[:50], str(report))
