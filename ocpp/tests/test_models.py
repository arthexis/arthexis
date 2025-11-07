from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.conftest  # noqa: F401

from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from core.models import EnergyTariff, Reference, SecurityGroup

from ocpp.models import (
    Charger,
    ChargerConfiguration,
    ConfigurationKey,
    Location,
)
from nodes.models import Node


class LocationEnergyTariffFieldsTests(TestCase):
    def test_zone_and_contract_type_use_energy_tariff_choices(self):
        zone_field = Location._meta.get_field("zone")
        contract_field = Location._meta.get_field("contract_type")

        self.assertEqual(zone_field.choices, EnergyTariff.Zone.choices)
        self.assertEqual(
            contract_field.choices, EnergyTariff.ContractType.choices
        )

    def test_location_stores_tariff_scope_information(self):
        location = Location.objects.create(
            name="HQ",
            zone=EnergyTariff.Zone.ONE_A,
            contract_type=EnergyTariff.ContractType.DAC,
        )

        location.refresh_from_db()

        self.assertEqual(location.zone, EnergyTariff.Zone.ONE_A)
        self.assertEqual(
            location.contract_type, EnergyTariff.ContractType.DAC
        )


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


class ChargerManagerNodeTests(TestCase):
    def test_manager_node_refreshed_using_local_lookup(self):
        local_node = Node.objects.create(hostname="local", address="127.0.0.1")

        with patch("nodes.models.Node.get_local", return_value=local_node), patch.object(
            Charger, "_full_url", return_value="http://example.com"
        ):
            managed = Charger.objects.create(charger_id="Managed-1")

        self.assertEqual(managed.manager_node_id, local_node.pk)
        persisted = Charger.objects.only("manager_node_id").get(pk=managed.pk)
        self.assertEqual(persisted.manager_node_id, local_node.pk)

        managed.manager_node = None
        managed.manager_node_id = None
        managed.firmware_status = "Installing"
        with patch("nodes.models.Node.get_local", return_value=local_node), patch.object(
            Charger, "_full_url", return_value="http://example.com"
        ):
            managed.save(update_fields={"firmware_status"})

        managed.refresh_from_db()
        self.assertEqual(managed.manager_node_id, local_node.pk)

        with patch("nodes.models.Node.get_local", return_value=None), patch.object(
            Charger, "_full_url", return_value="http://example.com"
        ):
            unmanaged = Charger.objects.create(charger_id="Unmanaged-1")

        self.assertIsNone(unmanaged.manager_node)

        unmanaged.manager_node = None
        unmanaged.manager_node_id = None
        unmanaged.firmware_status = "Installed"
        with patch("nodes.models.Node.get_local", return_value=None), patch.object(
            Charger, "_full_url", return_value="http://example.com"
        ):
            unmanaged.save(update_fields={"firmware_status"})

        unmanaged.refresh_from_db()
        self.assertIsNone(unmanaged.manager_node)
        self.assertIsNone(unmanaged.manager_node_id)


class ChargerConnectorLabelTests(TestCase):
    def test_connector_labels_use_letters(self):
        connector_a = Charger.objects.create(
            charger_id="LETTER-A", connector_id=1
        )
        connector_b = Charger.objects.create(
            charger_id="LETTER-B", connector_id=2
        )
        connector_c = Charger.objects.create(
            charger_id="LETTER-C", connector_id=3
        )

        self.assertEqual(connector_a.connector_letter, "A")
        self.assertEqual(str(connector_a.connector_label), "Connector A (Left)")
        self.assertEqual(connector_b.connector_letter, "B")
        self.assertEqual(str(connector_b.connector_label), "Connector B (Right)")
        self.assertEqual(connector_c.connector_letter, "C")
        self.assertEqual(str(connector_c.connector_label), "Connector C")

    def test_letter_conversion_handles_multiple_cycles(self):
        self.assertEqual(Charger.connector_letter_from_value(27), "AA")


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


class ChargerConfigurationKeyTests(TestCase):
    def test_replace_configuration_keys_creates_related_entries(self):
        configuration = ChargerConfiguration.objects.create(
            charger_identifier="CFG-KEYS"
        )

        configuration.replace_configuration_keys(
            [
                {
                    "key": "HeartbeatInterval",
                    "value": "300",
                    "readonly": True,
                    "channel": "A",
                },
                {
                    "key": "AuthorizeRemoteTxRequests",
                    "readonly": False,
                },
                {"key": ""},
                "invalid",
            ]
        )

        rows = list(
            ConfigurationKey.objects.filter(configuration=configuration)
            .order_by("position", "id")
        )
        self.assertEqual(len(rows), 2)

        first, second = rows
        self.assertEqual(first.key, "HeartbeatInterval")
        self.assertTrue(first.has_value)
        self.assertEqual(first.value, "300")
        self.assertTrue(first.readonly)
        self.assertEqual(first.extra_data, {"channel": "A"})

        self.assertEqual(second.key, "AuthorizeRemoteTxRequests")
        self.assertFalse(second.has_value)
        self.assertIsNone(second.value)
        self.assertFalse(second.readonly)
        self.assertEqual(second.extra_data, {})

        self.assertEqual(
            configuration.configuration_keys,
            [
                {
                    "key": "HeartbeatInterval",
                    "readonly": True,
                    "value": "300",
                    "channel": "A",
                },
                {
                    "key": "AuthorizeRemoteTxRequests",
                    "readonly": False,
                },
            ],
        )

    def test_replace_configuration_keys_overwrites_existing_entries(self):
        configuration = ChargerConfiguration.objects.create(
            charger_identifier="CFG-OVERWRITE"
        )

        configuration.replace_configuration_keys([
            {"key": "HeartbeatInterval", "value": "300"}
        ])
        configuration.replace_configuration_keys([
            {"key": "MeterValueSampleInterval", "value": 900}
        ])

        rows = list(
            ConfigurationKey.objects.filter(configuration=configuration)
            .order_by("position", "id")
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].key, "MeterValueSampleInterval")
        self.assertTrue(rows[0].has_value)
        self.assertEqual(rows[0].value, 900)
        self.assertEqual(
            configuration.configuration_keys,
            [
                {
                    "key": "MeterValueSampleInterval",
                    "value": 900,
                    "readonly": False,
                }
            ],
        )


def test_simulator_as_config_defaults(simulator: Simulator):
    config = simulator.as_config()

    assert isinstance(config, SimulatorConfig)
    assert config.username is None
    assert config.password is None
    assert config.configuration_keys == []
    assert config.configuration_unknown_keys == []


def test_simulator_ws_url_port_and_slash_handling(simulator: Simulator):
    simulator.host = "localhost"
    simulator.ws_port = 9000
    simulator.cp_path = "SIM"
    simulator.save(update_fields=["host", "ws_port", "cp_path"])

    assert simulator.ws_url == "ws://localhost:9000/SIM/"

    simulator.ws_port = None
    simulator.save(update_fields=["ws_port"])

    assert simulator.ws_url == "ws://localhost/SIM/"
