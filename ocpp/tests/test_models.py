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


class ChargerConnectorSlugTests(TestCase):
    def test_none_round_trip_uses_aggregate_slug(self):
        slug = Charger.connector_slug_from_value(None)
        self.assertEqual(slug, Charger.AGGREGATE_CONNECTOR_SLUG)
        self.assertIsNone(Charger.connector_value_from_slug(slug))

    def test_string_number_converts_to_integer(self):
        value = Charger.connector_value_from_slug("2")
        self.assertEqual(value, 2)
        self.assertEqual(Charger.connector_slug_from_value(value), "2")

    def test_integer_slug_returns_same_integer(self):
        self.assertEqual(Charger.connector_value_from_slug(5), 5)

    def test_invalid_slugs_raise_value_error(self):
        for invalid in ["abc", object()]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    Charger.connector_value_from_slug(invalid)
