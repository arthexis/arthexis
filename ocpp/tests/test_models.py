from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.conftest  # noqa: F401

from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from ocpp.models import Charger


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


class ChargerUrlFallbackTests(TestCase):
    @override_settings(
        DEFAULT_SITE_DOMAIN=" configured.example.com ",
        DEFAULT_DOMAIN="unused.example.com",
        ALLOWED_HOSTS=["primary.example.net"],
        DEFAULT_HTTP_PROTOCOL="https",
    )
    def test_full_url_prefers_configured_defaults(self):
        with patch(
            "ocpp.models.Site.objects.get_current", side_effect=Site.DoesNotExist
        ):
            aggregate = Charger.objects.create(charger_id="SERIAL-1")
            connector = Charger.objects.create(
                charger_id="SERIAL-1",
                connector_id=3,
            )

            aggregate_path = reverse("charger-page", args=["SERIAL-1"])
            connector_path = reverse("charger-page-connector", args=["SERIAL-1", "3"])

            self.assertEqual(aggregate.get_absolute_url(), aggregate_path)
            self.assertEqual(connector.get_absolute_url(), connector_path)
            self.assertEqual(aggregate.connector_slug, "all")
            self.assertEqual(connector.connector_slug, "3")

            expected_domain = "configured.example.com"
            expected_aggregate_url = f"https://{expected_domain}{aggregate_path}"
            expected_connector_url = f"https://{expected_domain}{connector_path}"

            self.assertEqual(aggregate._full_url(), expected_aggregate_url)
            self.assertEqual(connector._full_url(), expected_connector_url)

    @override_settings(
        DEFAULT_SITE_DOMAIN="",
        DEFAULT_DOMAIN="",
        ALLOWED_HOSTS=["", "*.ignored.example", " fallback.example.org ", "bad/host"],
        DEFAULT_HTTP_PROTOCOL="http",
    )
    def test_full_url_falls_back_to_allowed_hosts(self):
        with patch(
            "ocpp.models.Site.objects.get_current", side_effect=Site.DoesNotExist
        ):
            aggregate = Charger.objects.create(charger_id="SERIAL-2")
            connector = Charger.objects.create(
                charger_id="SERIAL-2",
                connector_id=5,
            )

            aggregate_path = reverse("charger-page", args=["SERIAL-2"])
            connector_path = reverse("charger-page-connector", args=["SERIAL-2", "5"])

            self.assertEqual(aggregate.get_absolute_url(), aggregate_path)
            self.assertEqual(connector.get_absolute_url(), connector_path)
            self.assertEqual(aggregate.connector_slug, "all")
            self.assertEqual(connector.connector_slug, "5")

            expected_domain = "fallback.example.org"
            expected_aggregate_url = f"http://{expected_domain}{aggregate_path}"
            expected_connector_url = f"http://{expected_domain}{connector_path}"

            self.assertEqual(aggregate._full_url(), expected_aggregate_url)
            self.assertEqual(connector._full_url(), expected_connector_url)
