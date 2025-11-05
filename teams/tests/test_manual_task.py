from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from nodes.models import Node
from ocpp.models import Location
from teams.models import ManualTask


class ManualTaskModelTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(
            hostname="test-node",
            mac_address="AA:BB:CC:DD:EE:01",
        )
        self.location = Location.objects.create(name="Depot")
        self.start = timezone.now()
        self.end = self.start + timedelta(hours=1)

    def test_requires_node_or_location(self):
        task = ManualTask(
            title="Inspect Router",
            description="Check wiring and record status.",
            scheduled_start=self.start,
            scheduled_end=self.end,
        )
        with self.assertRaises(ValidationError) as exc:
            task.full_clean()
        self.assertIn("node", exc.exception.error_dict)
        self.assertIn("location", exc.exception.error_dict)

    def test_enforces_schedule_order(self):
        task = ManualTask(
            title="Review Firmware",
            description="Validate upgrade plan.",
            node=self.node,
            scheduled_start=self.end,
            scheduled_end=self.start,
        )
        with self.assertRaises(ValidationError) as exc:
            task.full_clean()
        self.assertIn("scheduled_end", exc.exception.error_dict)

    def test_can_assign_to_node_and_location(self):
        task = ManualTask(
            title="Calibrate Charger",
            description="Confirm output settings.",
            node=self.node,
            location=self.location,
            scheduled_start=self.start,
            scheduled_end=self.end,
        )
        task.full_clean()
        task.save()
        self.assertEqual(str(task), "Calibrate Charger")
        self.assertEqual(ManualTask.objects.count(), 1)
        saved = ManualTask.objects.get()
        self.assertEqual(saved.node, self.node)
        self.assertEqual(saved.location, self.location)
