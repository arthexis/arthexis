"""Tests for operational status surface visibility and log redaction."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.ocpp import store
from apps.ocpp.models import Charger
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
