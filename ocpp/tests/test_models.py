from __future__ import annotations

import sys
from collections import deque
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.conftest  # noqa: F401

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from core.models import EnergyTariff, Location, Reference, SecurityGroup

from ocpp import store
from ocpp.models import (
    Charger,
    ChargerConfiguration,
    ConfigurationKey,
    generate_log_request_id,
)
from nodes.models import Node


class GenerateLogRequestIdTests(TestCase):
    def test_generate_log_request_id_clamps_and_substitutes_zero(self):
        max_request_id = (1 << 31) - 1
        representative_values = [0, 1, max_request_id]

        with patch("ocpp.models.secrets.randbits", side_effect=representative_values) as mocked_randbits:
            generated_ids = [generate_log_request_id() for _ in representative_values]

        self.assertEqual(mocked_randbits.call_count, len(representative_values))

        self.assertEqual(generated_ids[0], 1)
        self.assertEqual(generated_ids[1], 1)
        self.assertEqual(generated_ids[2], max_request_id)

        for generated_id in generated_ids:
            self.assertGreaterEqual(generated_id, 1)
            self.assertLessEqual(generated_id, max_request_id)

        follow_up_id = generate_log_request_id()
        self.assertGreaterEqual(follow_up_id, 1)
        self.assertLessEqual(follow_up_id, max_request_id)


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

    def test_location_tracks_contact_details(self):
        user_model = get_user_model()
        owner = user_model.objects.create_user(
            username="loc-owner",
            password="password",
            email="owner@example.com",
        )

        location = Location.objects.create(
            name="HQ",
            address_line1="123 Main St",
            address_line2="Suite 500",
            city="Monterrey",
            state="NL",
            postal_code="64000",
            country="MX",
            phone_number="+52 818 555 0101",
            assigned_to=owner,
        )

        location.refresh_from_db()

        self.assertEqual(location.address_line1, "123 Main St")
        self.assertEqual(location.address_line2, "Suite 500")
        self.assertEqual(location.city, "Monterrey")
        self.assertEqual(location.state, "NL")
        self.assertEqual(location.postal_code, "64000")
        self.assertEqual(location.country, "MX")
        self.assertEqual(location.phone_number, "+52 818 555 0101")
        self.assertEqual(location.assigned_to, owner)


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


class ChargerPurgeTests(TestCase):
    def test_purge_clears_related_data_and_store_caches(self):
        charger = Charger.objects.create(charger_id="SERIAL-1", connector_id=1)
        transaction = charger.transactions.create(
            start_time=timezone.now(),
            stop_time=timezone.now(),
            meter_start=1,
            meter_stop=2,
        )
        charger.meter_values.create(
            transaction=transaction,
            timestamp=timezone.now(),
            energy=Decimal("1.5"),
        )

        serial = charger.charger_id
        connector_key = store.identity_key(serial, charger.connector_id)
        aggregate_key = store.identity_key(serial, None)
        pending_key = store.pending_key(serial)
        base_key = serial

        fake_logs = {
            "charger": {
                connector_key: deque(["connector log"]),
                aggregate_key: deque(["aggregate log"]),
                pending_key: deque(["pending log"]),
                base_key: deque(["base log"]),
            },
            "simulator": {},
        }
        fake_transactions = {
            connector_key: object(),
            aggregate_key: object(),
            pending_key: object(),
            base_key: object(),
        }
        fake_history = {
            connector_key: {"value": 1},
            aggregate_key: {"value": 2},
            pending_key: {"value": 3},
            base_key: {"value": 4},
        }
        fake_log_names = {
            "charger": {
                connector_key: "SERIAL-1-1",
                aggregate_key: "SERIAL-1",
            },
            "simulator": {},
        }

        with patch.multiple(
            store,
            logs=fake_logs,
            transactions=fake_transactions,
            history=fake_history,
            log_names=fake_log_names,
        ):
            with self.assertRaises(ProtectedError):
                charger.delete()

            charger.purge()

            self.assertFalse(charger.transactions.exists())
            self.assertFalse(charger.meter_values.exists())
            self.assertEqual(fake_logs["charger"], {})
            self.assertEqual(fake_transactions, {})
            self.assertEqual(fake_history, {})

            charger.delete()

        self.assertFalse(Charger.objects.filter(pk=charger.pk).exists())

    def test_delete_purges_store_caches_when_no_db_data_remains(self):
        charger = Charger.objects.create(charger_id="SERIAL-2", connector_id=1)

        serial = charger.charger_id
        connector_key = store.identity_key(serial, charger.connector_id)
        aggregate_key = store.identity_key(serial, None)
        pending_key = store.pending_key(serial)
        base_key = serial

        fake_logs = {
            "charger": {
                connector_key: deque(["connector log"]),
                aggregate_key: deque(["aggregate log"]),
                pending_key: deque(["pending log"]),
                base_key: deque(["base log"]),
            },
            "simulator": {},
        }
        fake_transactions = {
            connector_key: object(),
            aggregate_key: object(),
            pending_key: object(),
            base_key: object(),
        }
        fake_history = {
            connector_key: {"value": 1},
            aggregate_key: {"value": 2},
            pending_key: {"value": 3},
            base_key: {"value": 4},
        }
        fake_log_names = {
            "charger": {
                connector_key: "SERIAL-2-1",
                aggregate_key: "SERIAL-2",
            },
            "simulator": {},
        }

        with patch.multiple(
            store,
            logs=fake_logs,
            transactions=fake_transactions,
            history=fake_history,
            log_names=fake_log_names,
        ):
            charger.delete()

            self.assertEqual(fake_logs["charger"], {})
            self.assertEqual(fake_transactions, {})
            self.assertEqual(fake_history, {})

        self.assertFalse(Charger.objects.filter(pk=charger.pk).exists())


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


class ChargerLocalOriginTests(TestCase):
    def setUp(self):
        self.local_node = Node.objects.create(
            hostname="local-node", address="127.0.0.1"
        )
        self.remote_node = Node.objects.create(
            hostname="remote-node", address="10.0.0.1"
        )

    def test_is_local_true_when_origin_matches_local_node(self):
        charger = Charger.objects.create(
            charger_id="LOCAL-MATCH", node_origin=self.local_node
        )

        with patch("nodes.models.Node.get_local", return_value=self.local_node):
            self.assertTrue(charger.is_local)

    def test_is_local_false_when_local_node_missing(self):
        charger = Charger.objects.create(
            charger_id="NO-LOCAL", node_origin=self.remote_node
        )

        with patch("nodes.models.Node.get_local", return_value=None):
            self.assertFalse(charger.is_local)

    def test_is_local_false_when_origin_differs_from_local(self):
        charger = Charger.objects.create(
            charger_id="REMOTE-ORIGIN", node_origin=self.remote_node
        )

        with patch("nodes.models.Node.get_local", return_value=self.local_node):
            self.assertFalse(charger.is_local)

    def test_is_local_true_when_origin_missing_but_local_exists(self):
        with patch("nodes.models.Node.get_local", return_value=None):
            charger = Charger.objects.create(charger_id="LOCAL-UNTRACKED")

        charger.refresh_from_db()
        self.assertIsNone(charger.node_origin_id)

        with patch("nodes.models.Node.get_local", return_value=self.local_node):
            self.assertTrue(charger.is_local)


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

    def test_connector_slug_from_value_and_back(self):
        self.assertEqual(Charger.connector_slug_from_value(None), "all")
        self.assertEqual(Charger.connector_slug_from_value(5), "5")

        self.assertIsNone(Charger.connector_value_from_slug(None))
        self.assertIsNone(Charger.connector_value_from_slug(""))
        self.assertIsNone(Charger.connector_value_from_slug("all"))
        self.assertEqual(Charger.connector_value_from_slug(7), 7)
        self.assertEqual(Charger.connector_value_from_slug("8"), 8)

        with self.assertRaises(ValueError):
            Charger.connector_value_from_slug("AA")

    def test_connector_letter_from_slug_handles_multi_letter(self):
        self.assertEqual(Charger.connector_letter_from_slug(27), "AA")
        self.assertEqual(Charger.connector_letter_from_slug("28"), "AB")
        self.assertIsNone(Charger.connector_letter_from_slug(None))

    def test_connector_value_from_letter_normalizes_case(self):
        self.assertEqual(Charger.connector_value_from_letter("a"), 1)
        self.assertEqual(Charger.connector_value_from_letter("A"), 1)
        self.assertEqual(Charger.connector_value_from_letter("Z"), 26)
        self.assertEqual(Charger.connector_value_from_letter("aa"), 27)

        with self.assertRaisesMessage(ValueError, "Connector label is required"):
            Charger.connector_value_from_letter("")

        with self.assertRaises(ValueError):
            Charger.connector_value_from_letter("A1")

    def test_identity_helpers_handle_aggregate_connector(self):
        charger = Charger.objects.create(charger_id="AGGREGATE-1")

        self.assertIsNone(charger.connector_id)
        self.assertEqual(str(charger.connector_label), "All Connectors")
        self.assertEqual(charger.identity_tuple(), ("AGGREGATE-1", None))
        self.assertEqual(charger.identity_slug(), "AGGREGATE-1#all")


class ChargerVisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()

        cls.owner_user = user_model.objects.create_user(
            username="owner",
            password="secret-owner",
            email="owner@example.com",
        )
        cls.staff_user = user_model.objects.create_user(
            username="staff",
            password="secret-staff",
            email="staff@example.com",
            is_staff=True,
        )
        cls.group_member = user_model.objects.create_user(
            username="group-member",
            password="secret-group",
            email="group@example.com",
        )
        cls.superuser = user_model.objects.create_superuser(
            username="supervisor",
            password="secret-super",
            email="super@example.com",
        )

        cls.security_group = SecurityGroup.objects.create(name="Maintenance")
        cls.security_group.user_set.add(cls.group_member)

        cls.public_charger = Charger.objects.create(
            charger_id="PUB-1",
            public_display=True,
        )
        cls.user_owned_charger = Charger.objects.create(
            charger_id="USR-1",
            public_display=True,
        )
        cls.user_owned_charger.owner_users.add(cls.owner_user)

        cls.group_owned_charger = Charger.objects.create(
            charger_id="GRP-1",
            public_display=True,
        )
        cls.group_owned_charger.owner_groups.add(cls.security_group)

    def test_has_owner_scope_tracks_ownership_assignments(self):
        self.assertFalse(self.public_charger.has_owner_scope())
        self.assertTrue(self.user_owned_charger.has_owner_scope())
        self.assertTrue(self.group_owned_charger.has_owner_scope())

    def test_is_visible_to_follows_query_rules(self):
        anonymous = AnonymousUser()
        test_cases = [
            (anonymous, {"PUB-1"}),
            (self.staff_user, {"PUB-1"}),
            (self.owner_user, {"PUB-1", "USR-1"}),
            (self.group_member, {"PUB-1", "GRP-1"}),
            (self.superuser, {"PUB-1", "USR-1", "GRP-1"}),
        ]

        chargers = [
            self.public_charger,
            self.user_owned_charger,
            self.group_owned_charger,
        ]

        for user, expected_visible in test_cases:
            with self.subTest(user=getattr(user, "username", "anonymous")):
                queryset_ids = set(
                    Charger.visible_for_user(user).values_list(
                        "charger_id", flat=True
                    )
                )
                self.assertSetEqual(queryset_ids, expected_visible)


                for charger in chargers:
                    with self.subTest(charger=charger.charger_id):
                        should_be_visible = charger.charger_id in expected_visible
                        self.assertEqual(
                            charger.is_visible_to(user),
                            should_be_visible,
                        )


class ChargerWSAuthTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="ws-user", password="secret", email="ws@example.com"
        )
        self.other = user_model.objects.create_user(
            username="ws-other", password="secret", email="other@example.com"
        )
        self.group = SecurityGroup.objects.create(name="WS QA")
        self.group.user_set.add(self.user)

    def test_clean_rejects_user_and_group(self):
        charger = Charger(charger_id="AUTH-CLEAN")
        charger.ws_auth_user = self.user
        charger.ws_auth_group = self.group

        with self.assertRaises(ValidationError):
            charger.clean()

    def test_requires_ws_auth_property(self):
        charger = Charger.objects.create(charger_id="AUTH-REQ")
        self.assertFalse(charger.requires_ws_auth)

        charger.ws_auth_user = self.user
        self.assertTrue(charger.requires_ws_auth)

    def test_is_ws_user_authorized_matches_expected_subjects(self):
        user_scoped = Charger.objects.create(
            charger_id="AUTH-USER", ws_auth_user=self.user
        )
        self.assertTrue(user_scoped.is_ws_user_authorized(self.user))
        self.assertFalse(user_scoped.is_ws_user_authorized(self.other))

        group_scoped = Charger.objects.create(
            charger_id="AUTH-GROUP", ws_auth_group=self.group
        )
        self.assertTrue(group_scoped.is_ws_user_authorized(self.user))
        self.assertFalse(group_scoped.is_ws_user_authorized(self.other))


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
