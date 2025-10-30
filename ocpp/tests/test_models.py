from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.conftest  # noqa: F401

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from core.models import Reference

from ocpp.models import Charger
from core.models import SecurityGroup


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


class ChargerReferenceTests(TestCase):
    def test_reference_created_and_updated_for_remote_urls(self):
        serial = "Remote-123"
        first_url = "http://remote.example/chargers/remote-123"
        updated_url = "http://remote.example/chargers/remote-123/v2"

        with patch("ocpp.models.url_targets_local_loopback") as loopback_mock, patch.object(
            Charger, "_full_url"
        ) as full_url_mock:
            loopback_mock.return_value = False
            full_url_mock.return_value = first_url

            charger = Charger.objects.create(charger_id=serial)

            charger.refresh_from_db()
            self.assertIsNotNone(charger.reference)
            self.assertEqual(charger.reference.value, first_url)
            self.assertEqual(Reference.objects.count(), 1)

            existing_reference_id = charger.reference_id

            full_url_mock.return_value = first_url
            charger.save()
            charger.refresh_from_db()

            self.assertEqual(Reference.objects.count(), 1)
            self.assertEqual(charger.reference_id, existing_reference_id)
            self.assertEqual(charger.reference.value, first_url)

            full_url_mock.return_value = updated_url
            charger.save()
            charger.refresh_from_db()

            self.assertEqual(Reference.objects.count(), 1)
            self.assertEqual(charger.reference_id, existing_reference_id)
            self.assertEqual(charger.reference.value, updated_url)

    def test_loopback_url_skips_reference_creation(self):
        serial = "Loopback-123"

        with patch("ocpp.models.url_targets_local_loopback") as loopback_mock, patch.object(
            Charger, "_full_url", return_value="http://loopback"
        ):
            loopback_mock.return_value = True

            charger = Charger.objects.create(charger_id=serial)

            charger.refresh_from_db()
            self.assertIsNone(charger.reference)

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
