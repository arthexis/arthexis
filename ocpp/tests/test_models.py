from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.conftest  # noqa: F401

from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from ocpp import store
from ocpp.models import Charger, MeterReading, Transaction


class ChargerAutoLocationNameTests(TestCase):
    def test_sanitize_auto_location_name_collapses_and_falls_back(self):
        cases = [
            ("  Main Street 42  ", "Main_Street_42"),
            ("Dock & Co.", "Dock_Co"),
            ("___", "Charger"),
        ]

        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(
                    Charger.sanitize_auto_location_name(raw), expected
                )

    def test_location_created_and_reused_with_update_fields(self):
        serial = " ACME*/HQ "
        expected_name = Charger.sanitize_auto_location_name(serial)

        with patch.object(Charger, "_full_url", return_value="http://example.com"):
            charger = Charger.objects.create(charger_id=serial)

        charger.refresh_from_db()
        self.assertIsNotNone(charger.location)
        self.assertEqual(charger.location.name, expected_name)

        with patch.object(Charger, "_full_url", return_value="http://example.com"):
            connector = Charger.objects.create(
                charger_id=serial,
                connector_id=1,
                firmware_status="Installing",
            )

        connector.refresh_from_db()
        connector.location = None
        connector.location_id = None
        connector.firmware_status = "Installed"

        with patch.object(Charger, "_full_url", return_value="http://example.com"):
            connector.save(update_fields={"firmware_status"})
        connector.refresh_from_db()

        self.assertIsNotNone(connector.location)
        self.assertEqual(connector.location_id, charger.location_id)
        self.assertEqual(connector.location.name, expected_name)

    def test_punctuation_only_serial_uses_generic_name(self):
        with patch.object(Charger, "_full_url", return_value="http://example.com"):
            charger = Charger.objects.create(charger_id="***")
        charger.refresh_from_db()

        self.assertIsNotNone(charger.location)
        self.assertEqual(charger.location.name, "Charger")
        self.assertEqual(
            Charger.sanitize_auto_location_name(charger.charger_id), "Charger"
        )


class ChargerSerialValidationTests(TestCase):
    def test_validate_serial_strips_and_rejects_invalid_values(self):
        self.assertEqual(Charger.validate_serial("  ABC  "), "ABC")

        for value, expected_message in (
            (None, "Serial Number cannot be blank."),
            ("", "Serial Number cannot be blank."),
            (
                "<charger_id>",
                "Serial Number placeholder values such as <charger_id> are not allowed.",
            ),
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError) as context:
                    Charger.validate_serial(value)

                message_dict = context.exception.message_dict
                self.assertIn("charger_id", message_dict)
                self.assertIn(expected_message, message_dict["charger_id"])

    def test_full_clean_propagates_placeholder_serial_error(self):
        charger = Charger(charger_id="<invalid>")

        with self.assertRaises(ValidationError) as context:
            charger.full_clean()

        message_dict = context.exception.message_dict
        self.assertIn("charger_id", message_dict)
        self.assertIn(
            "Serial Number placeholder values such as <charger_id> are not allowed.",
            message_dict["charger_id"],
        )


class ChargerPurgeTests(TestCase):
    def setUp(self):
        super().setUp()
        store.logs["charger"].clear()
        store.transactions.clear()
        store.history.clear()
        self.addCleanup(store.logs["charger"].clear)
        self.addCleanup(store.transactions.clear)
        self.addCleanup(store.history.clear)

    def test_delete_requires_purge_for_aggregate_and_connectors(self):
        serial = "PURGEAGG"
        now = timezone.now()

        charger = Charger.objects.create(charger_id=serial)
        connector = Charger.objects.create(charger_id=serial, connector_id=1)

        Transaction.objects.create(charger=charger, start_time=now)
        Transaction.objects.create(
            charger=connector,
            start_time=now,
            connector_id=1,
        )
        MeterReading.objects.create(
            charger=charger,
            timestamp=now,
            value=1,
        )
        MeterReading.objects.create(
            charger=connector,
            connector_id=1,
            timestamp=now,
            value=2,
        )

        aggregate_key = store.identity_key(serial, None)
        connector_key = store.identity_key(serial, 1)
        pending_key = store.pending_key(serial)

        store.logs["charger"][aggregate_key] = ["aggregate log"]
        store.logs["charger"][connector_key] = ["connector log"]
        store.logs["charger"][pending_key] = ["pending log"]
        store.logs["charger"][serial] = ["base log"]

        store.transactions[aggregate_key] = object()
        store.transactions[connector_key] = object()
        store.transactions[pending_key] = object()
        store.transactions[serial] = object()

        store.history[aggregate_key] = {"history": "aggregate"}
        store.history[connector_key] = {"history": "connector"}
        store.history[pending_key] = {"history": "pending"}
        store.history[serial] = {"history": "base"}

        with self.assertRaises(ProtectedError):
            charger.delete()

        charger.refresh_from_db()
        connector.refresh_from_db()

        charger.purge()

        self.assertFalse(Transaction.objects.filter(charger__charger_id=serial).exists())
        self.assertFalse(MeterReading.objects.filter(charger__charger_id=serial).exists())

        expected_keys = {aggregate_key, connector_key, pending_key, serial}
        for key in expected_keys:
            self.assertNotIn(key, store.logs["charger"])
            self.assertNotIn(key, store.transactions)
            self.assertNotIn(key, store.history)

        charger.delete()
        self.assertFalse(Charger.objects.filter(pk=charger.pk).exists())

        connector.delete()
        self.assertFalse(Charger.objects.filter(pk=connector.pk).exists())
        self.assertFalse(Charger.objects.filter(charger_id=serial).exists())
