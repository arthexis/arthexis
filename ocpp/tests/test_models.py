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
            (
                self.group_user,
                {self.public_charger.pk, self.group_restricted_charger.pk},
            ),
            (
                self.superuser,
                {charger.pk for charger in self.all_chargers},
            ),
        ]

        for user, expected in scenarios:
            queryset_ids = {charger.pk for charger in Charger.visible_for_user(user)}
            direct_ids = {
                charger.pk
                for charger in self.all_chargers
                if charger.is_visible_to(user)
            }

            self.assertEqual(expected, queryset_ids)
            self.assertEqual(queryset_ids, direct_ids)
