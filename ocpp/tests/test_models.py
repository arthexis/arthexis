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


class ChargerVisibilityScopeTests(TestCase):
    def setUp(self):
        super().setUp()
        patcher = patch.object(Charger, "_full_url", return_value="http://example.com")
        patcher.start()
        self.addCleanup(patcher.stop)

        user_model = get_user_model()
        self.superuser = user_model.objects.create_superuser(
            username="supervisor",
            email="supervisor@example.com",
            password="password",
        )
        self.owner_user = user_model.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="password",
        )
        self.group_user = user_model.objects.create_user(
            username="groupie",
            email="group@example.com",
            password="password",
        )

        self.security_group = SecurityGroup.objects.create(name="Fleet Access")
        self.group_user.groups.add(self.security_group)

        self.public_charger = Charger.objects.create(charger_id="public-serial")
        self.user_restricted_charger = Charger.objects.create(
            charger_id="user-serial"
        )
        self.user_restricted_charger.owner_users.add(self.owner_user)
        self.group_restricted_charger = Charger.objects.create(
            charger_id="group-serial"
        )
        self.group_restricted_charger.owner_groups.add(self.security_group)

        self.all_chargers = [
            self.public_charger,
            self.user_restricted_charger,
            self.group_restricted_charger,
        ]

    def test_visible_for_user_honors_user_and_group_scope(self):
        anonymous_visible = Charger.visible_for_user(AnonymousUser())
        self.assertEqual(
            {self.public_charger.pk},
            {charger.pk for charger in anonymous_visible},
        )

        owner_visible = Charger.visible_for_user(self.owner_user)
        self.assertEqual(
            {self.public_charger.pk, self.user_restricted_charger.pk},
            {charger.pk for charger in owner_visible},
        )

        group_visible = Charger.visible_for_user(self.group_user)
        self.assertEqual(
            {self.public_charger.pk, self.group_restricted_charger.pk},
            {charger.pk for charger in group_visible},
        )

        superuser_visible = Charger.visible_for_user(self.superuser)
        self.assertEqual(
            {charger.pk for charger in self.all_chargers},
            {charger.pk for charger in superuser_visible},
        )

    def test_instance_visibility_matches_queryset_logic(self):
        self.assertFalse(self.public_charger.has_owner_scope())
        self.assertTrue(self.user_restricted_charger.has_owner_scope())
        self.assertTrue(self.group_restricted_charger.has_owner_scope())

        scenarios = [
            (AnonymousUser(), {self.public_charger.pk}),
            (
                self.owner_user,
                {self.public_charger.pk, self.user_restricted_charger.pk},
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
