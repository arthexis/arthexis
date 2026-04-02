from datetime import timedelta

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.ocpp.admin.charge_point.admin import ChargerAdmin
from apps.ocpp.models import Charger, ControlOperationEvent, SecurityEvent, Transaction


class OperatorAdminSurfacesTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = ChargerAdmin(Charger, self.site)
        self.superuser = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(self.superuser)
        self.charger = Charger.objects.create(
            charger_id="CP-OPS-1",
            availability_state="Operative",
            last_online_at=timezone.now(),
        )

    def test_action_history_link_filters_current_charger(self):
        link = self.admin.action_history_link(self.charger)

        self.assertIn("charger__id__exact", link)
        self.assertIn(str(self.charger.pk), link)

    def test_operations_health_context_counts_recent_failures(self):
        now = timezone.now()
        Transaction.objects.create(
            charger=self.charger,
            start_time=now - timedelta(hours=3),
            authorization_status=Transaction.AuthorizationStatus.REJECTED,
            authorization_reason="TokenExpired",
            rejected_at=now - timedelta(hours=3),
        )
        ControlOperationEvent.objects.create(
            charger=self.charger,
            action="Reset",
            transport=ControlOperationEvent.Transport.LOCAL,
            status=ControlOperationEvent.Status.FAILED,
            detail="No response",
        )
        SecurityEvent.objects.create(
            charger=self.charger,
            event_type="TamperDetected",
            event_timestamp=now - timedelta(hours=2),
        )

        context = self.admin._operations_health_context(self.charger)
        overview = context["ops_health_overview"]

        self.assertEqual(overview["rejected_sessions_24h"], 1)
        self.assertEqual(overview["failed_ops_24h"], 1)
        self.assertEqual(overview["security_events_24h"], 1)

    def test_change_form_renders_all_non_reference_fieldsets(self):
        response = self.client.get(
            reverse("admin:ocpp_charger_change", args=[self.charger.pk])
        )

        self.assertContains(response, "Availability")
        self.assertContains(response, "Network")
        self.assertContains(response, "Authentication")
