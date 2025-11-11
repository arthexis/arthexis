import os
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.utils import timezone

from django.apps import apps

from nodes.models import Node
from teams.models import EmailOutbox
from ocpp.models import CPReservation, Charger
from core.models import Location
from teams.admin import ManualTaskAdmin
from teams.models import ManualTask, SecurityGroup, TaskCategory


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

    def test_can_assign_category(self):
        category = TaskCategory.objects.create(name="Maintenance")
        task = ManualTask(
            title="Inspect Charger",
            description="Verify connectors are clean.",
            node=self.node,
            scheduled_start=self.start,
            scheduled_end=self.end,
            category=category,
        )
        task.full_clean()
        task.save()
        self.assertEqual(task.category, category)


class ManualTaskNotificationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="sam",
            email="sam@example.com",
            password="pwd",
        )
        self.group = SecurityGroup.objects.create(name="Operators")
        self.group.user_set.add(
            get_user_model().objects.create_user(
                username="alex",
                email="alex@example.com",
                password="pwd",
            )
        )
        self.node = Node.objects.create(
            hostname="notifier",
            mac_address="AA:BB:CC:DD:EE:10",
        )
        EmailOutbox.objects.create(
            node=self.node,
            user=get_user_model().objects.create_user(
                username="owner",
                email="owner@example.com",
                password="pwd",
            ),
            host="smtp.example.com",
            port=25,
            from_email="noreply@example.com",
        )

    def _celery_lock(self) -> Path:
        lock_path = Path(settings.BASE_DIR) / "locks" / "celery.lck"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        return lock_path

    def test_schedule_notifications_requires_celery_lock(self):
        start = timezone.now() + timedelta(days=2)
        end = start + timedelta(hours=1)
        lock = self._celery_lock()
        if lock.exists():
            lock.unlink()

        with mock.patch("teams.tasks.send_manual_task_notification.apply_async") as apply_async, mock.patch(
            "teams.models.mailer.can_send_email", return_value=True
        ):
            ManualTask.objects.create(
                title="Prep",
                description="Prep chargers",
                node=self.node,
                scheduled_start=start,
                scheduled_end=end,
                assigned_user=self.user,
                enable_notifications=True,
            )
            apply_async.assert_not_called()

        lock.touch()
        self.addCleanup(lambda: os.path.exists(lock) and lock.unlink())
        with mock.patch("teams.tasks.send_manual_task_notification.apply_async") as apply_async, mock.patch(
            "teams.models.mailer.can_send_email", return_value=True
        ):
            ManualTask.objects.create(
                title="Prep",
                description="Prep chargers",
                node=self.node,
                scheduled_start=start,
                scheduled_end=end,
                assigned_user=self.user,
                enable_notifications=True,
            )
            self.assertEqual(apply_async.call_count, 3)

    def test_send_notification_email_combines_recipients(self):
        task = ManualTask.objects.create(
            title="Inspect",
            description="Inspect chargers",
            node=self.node,
            scheduled_start=timezone.now() + timedelta(hours=4),
            scheduled_end=timezone.now() + timedelta(hours=5),
            assigned_user=self.user,
            assigned_group=self.group,
            enable_notifications=True,
        )

        with mock.patch("nodes.models.Node.send_mail") as send_mail:
            task.send_notification_email("immediate")

        self.assertTrue(send_mail.called)
        args, kwargs = send_mail.call_args
        recipients = set(args[2]) if len(args) >= 3 else set(kwargs.get("recipient_list", []))
        self.assertIn("sam@example.com", recipients)
        self.assertIn("alex@example.com", recipients)
        self.assertIn("owner@example.com", recipients)


class ManualTaskAdminActionTests(TestCase):
    def setUp(self):
        CustomerAccount = apps.get_model("core", "CustomerAccount")
        RFID = apps.get_model("core", "RFID")

        self.user = get_user_model().objects.create_user(
            username="planner",
            email="planner@example.com",
            password="pwd",
        )
        self.node = Node.objects.create(
            hostname="admin-node",
            mac_address="AA:BB:CC:DD:EE:20",
        )
        self.location = Location.objects.create(name="Service Bay")
        self.charger = Charger.objects.create(
            charger_id="SV123", location=self.location, connector_id=1
        )
        self.aggregate = Charger.objects.create(
            charger_id="SV123", location=self.location
        )
        self.account = CustomerAccount.objects.create(name="Planner Account", user=self.user)
        self.rfid = RFID.objects.create(rfid="ABCDEF12")
        self.account.rfids.add(self.rfid)
        self.start = timezone.now() + timedelta(hours=2)
        self.end = self.start + timedelta(minutes=90)

    def _build_request(self):
        factory = RequestFactory()
        request = factory.post("/admin/teams/manualtask/")
        setattr(request, "session", self.client.session)
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)
        return request

    def test_make_cp_reservations_action_creates_reservation(self):
        task = ManualTask.objects.create(
            title="Reserve Slot",
            description="Reserve connector",
            node=self.node,
            location=self.location,
            scheduled_start=self.start,
            scheduled_end=self.end,
            assigned_user=self.user,
        )
        admin_instance = ManualTaskAdmin(ManualTask, admin.site)

        request = self._build_request()
        with mock.patch(
            "ocpp.models.CPReservation.send_reservation_request", return_value=None
        ):
            admin_instance.make_cp_reservations(
                request, ManualTask.objects.filter(pk=task.pk)
            )

        stored_messages = list(request._messages)
        self.assertTrue(
            any("Created" in message.message for message in stored_messages),
            stored_messages,
        )
        self.assertEqual(CPReservation.objects.count(), 1)
        reservation = CPReservation.objects.first()
        self.assertEqual(reservation.location, self.location)
        self.assertEqual(reservation.account, self.account)
        self.assertEqual(reservation.rfid, self.rfid)
        self.assertEqual(reservation.duration_minutes, 90)
