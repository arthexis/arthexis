"""Tests for operational status surface visibility and log redaction."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.ocpp import store
from apps.ocpp.models import Charger, ControlOperationEvent
from apps.ops.models import SecurityAlertEvent
from apps.ops.status_surface import redact_log_line


class StatusSurfaceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="owner", password="x")
        self.intruder = User.objects.create_user(username="intruder", password="x")
        self.staff = User.objects.create_user(
            username="ops-staff",
            password="x",
            is_staff=True,
            is_superuser=True,
        )
        self.owner_charger = Charger.objects.create(charger_id="CP-OWNER")
        self.owner_charger.owner_users.add(self.owner)
        self.other_charger = Charger.objects.create(charger_id="CP-OTHER")
        self.other_charger.owner_users.add(self.intruder)

        owner_key = store.identity_key("CP-OWNER", None)
        other_key = store.identity_key("CP-OTHER", None)
        store.add_log(
            owner_key,
            'Authorize processed: {"token":"tok-secret","idTag":"A1"}',
            log_type="charger",
        )
        store.add_log(
            owner_key,
            "Connected",
            log_type="charger",
        )
        store.add_log(
            owner_key,
            "SecurityEventNotification: Authorization: Bearer secret-token-value",
            log_type="charger",
        )
        store.add_log(
            other_key,
            'DataTransfer received: {"password":"private","status":"ok"}',
            log_type="charger",
        )

    def tearDown(self):
        store.clear_log(store.identity_key("CP-OWNER", None), log_type="charger")
        store.clear_log(store.identity_key("CP-OTHER", None), log_type="charger")
        store.pending_calls.clear()
        store.monitoring_report_requests.clear()

    def test_redact_log_line_masks_secret_values(self):
        redacted = redact_log_line(
            'Authorize processed: {"token":"tok-secret","password":"abc","note":"ok"}'
        )

        self.assertNotIn("tok-secret", redacted)
        self.assertNotIn('"abc"', redacted)
        self.assertIn('"token": "[REDACTED]"', redacted)
        self.assertIn('"password": "[REDACTED]"', redacted)

    def test_status_surface_requires_authentication(self):
        response = self.client.get(reverse("ops:status-surface"))

        self.assertEqual(response.status_code, 302)

    def test_status_logs_are_tenant_scoped_and_sensitive_events_hidden_for_non_staff(self):
        self.client.force_login(self.owner)

        response = self.client.get(reverse("ops:status-logs"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        excerpts = payload["log_excerpts"]
        self.assertEqual(len(excerpts), 1)
        self.assertEqual(excerpts[0]["charger_id"], "CP-OWNER")
        lines = "\n".join(item["line"] for item in excerpts[0]["entries"])
        self.assertNotIn("SecurityEventNotification", lines)
        self.assertNotIn("tok-secret", lines)

    def test_status_logs_include_sensitive_event_names_for_staff_but_redact_tokens(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("ops:status-logs"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        joined_lines = "\n".join(
            entry["line"]
            for excerpt in payload["log_excerpts"]
            for entry in excerpt["entries"]
        )
        self.assertIn("SecurityEventNotification", joined_lines)
        self.assertNotIn("secret-token-value", joined_lines)
        self.assertIn("[REDACTED]", joined_lines)

    def test_status_surface_scopes_failed_operations_by_visible_charger_pk(self):
        owner_connector_one = Charger.objects.create(charger_id="CP-SHARED", connector_id=1)
        owner_connector_one.owner_users.add(self.owner)
        hidden_connector_two = Charger.objects.create(charger_id="CP-SHARED", connector_id=2)
        hidden_connector_two.owner_users.add(self.staff)
        ControlOperationEvent.objects.create(
            charger=owner_connector_one,
            actor=self.owner,
            action="RemoteStopTransaction",
            transport=ControlOperationEvent.Transport.LOCAL,
            status=ControlOperationEvent.Status.FAILED,
            detail="Owner-visible failure",
        )
        ControlOperationEvent.objects.create(
            charger=hidden_connector_two,
            actor=self.staff,
            action="RemoteStopTransaction",
            transport=ControlOperationEvent.Transport.LOCAL,
            status=ControlOperationEvent.Status.FAILED,
            detail="Hidden failure",
        )

        self.client.force_login(self.owner)
        response = self.client.get(reverse("ops:status-surface"))

        self.assertEqual(response.status_code, 200)
        events = response.json()["recent_critical_events"]
        details = "\n".join(event["details"] for event in events if event["source"] == "control_operation")
        self.assertIn("Owner-visible failure", details)
        self.assertNotIn("Hidden failure", details)

    def test_status_surface_includes_security_alerts_only_for_staff(self):
        SecurityAlertEvent.objects.create(
            key="sec-alert-1",
            severity="critical",
            message="Security alert present",
            detail="Authorization: Bearer visible-secret",
            last_occurred_at=timezone.now(),
            is_active=True,
        )

        self.client.force_login(self.owner)
        tenant_response = self.client.get(reverse("ops:status-surface"))
        tenant_events = tenant_response.json()["recent_critical_events"]
        self.assertFalse(any(event["source"] == "security_alert" for event in tenant_events))

        self.client.force_login(self.staff)
        staff_response = self.client.get(reverse("ops:status-surface"))
        staff_events = staff_response.json()["recent_critical_events"]
        security_events = [event for event in staff_events if event["source"] == "security_alert"]
        self.assertEqual(len(security_events), 1)
        self.assertNotIn("visible-secret", security_events[0]["details"])

    def test_status_surface_scopes_queue_health_to_tenant_visibility(self):
        owner_key = store.identity_key("CP-OWNER", None)
        other_key = store.identity_key("CP-OTHER", None)
        store.pending_calls["owner-call"] = {"log_key": owner_key}
        store.pending_calls["other-call"] = {"log_key": other_key}
        store.monitoring_report_requests[11] = {"charger_id": "CP-OWNER", "connector_id": None}
        store.monitoring_report_requests[12] = {"charger_id": "CP-OTHER", "connector_id": None}

        self.client.force_login(self.owner)
        owner_response = self.client.get(reverse("ops:status-surface"))
        owner_queue = owner_response.json()["service_health"]["queue"]
        self.assertEqual(owner_queue["pending_calls"], 1)
        self.assertEqual(owner_queue["monitoring_requests"], 1)

        self.client.force_login(self.staff)
        staff_response = self.client.get(reverse("ops:status-surface"))
        staff_queue = staff_response.json()["service_health"]["queue"]
        self.assertEqual(staff_queue["pending_calls"], 2)
        self.assertEqual(staff_queue["monitoring_requests"], 2)
