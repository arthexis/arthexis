"""Tests for the ``chargers`` management command websocket auth options."""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.ocpp import store
from apps.ocpp.models import Charger


class ChargersCommandTests(TestCase):
    """Validate websocket authentication toggles in the chargers CLI command."""

    def test_sets_ws_auth_user_with_password(self) -> None:
        """Setting websocket auth binds the matched charger and persists credentials."""

        charger = Charger.objects.create(charger_id="CLI-WS-1")

        call_command(
            "chargers",
            "--sn",
            charger.charger_id,
            "--ws-auth-username",
            "cp-user",
            "--ws-auth-password",
            "secret123",
        )

        charger.refresh_from_db()
        user = get_user_model().objects.get(username="cp-user")
        self.assertEqual(charger.ws_auth_user_id, user.pk)
        self.assertIsNone(charger.ws_auth_group_id)
        self.assertTrue(user.check_password("secret123"))

    def test_clears_ws_auth_protection(self) -> None:
        """Clearing websocket auth removes both user and group protection fields."""

        user_model = get_user_model()
        user = user_model.objects.create_user(username="bound-user", password="startpass")
        charger = Charger.objects.create(charger_id="CLI-WS-2", ws_auth_user=user)

        call_command("chargers", "--sn", charger.charger_id, "--ws-auth-clear")

        charger.refresh_from_db()
        self.assertIsNone(charger.ws_auth_user_id)
        self.assertIsNone(charger.ws_auth_group_id)

    def test_requires_password_when_username_is_provided(self) -> None:
        """Username-based websocket auth requires an explicit password option."""

        Charger.objects.create(charger_id="CLI-WS-3")

        with self.assertRaisesMessage(CommandError, "requires --ws-auth-password"):
            call_command("chargers", "--sn", "CLI-WS-3", "--ws-auth-username", "cp-user")

    def test_requires_effective_cp_selector_for_ws_auth_changes(self) -> None:
        """Whitespace-only ``--cp`` values do not bypass selector validation."""

        Charger.objects.create(charger_id="CLI-WS-4")

        with self.assertRaisesMessage(CommandError, "Websocket auth changes require"):
            call_command(
                "chargers",
                "--cp",
                "   ",
                "--ws-auth-clear",
            )

    def test_reactivates_existing_inactive_ws_auth_user(self) -> None:
        """Updating existing websocket auth credentials reactivates the user."""

        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="inactive-user",
            password="oldpass",
            is_active=False,
        )
        charger = Charger.objects.create(charger_id="CLI-WS-5")

        call_command(
            "chargers",
            "--sn",
            charger.charger_id,
            "--ws-auth-username",
            user.username,
            "--ws-auth-password",
            "newpass123",
        )

        user.refresh_from_db()
        charger.refresh_from_db()
        self.assertEqual(charger.ws_auth_user_id, user.pk)
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("newpass123"))

    def test_rename_base_charger_renames_connectors_automatically(self) -> None:
        """Renaming a base charger updates connector names with letter suffixes."""

        Charger.objects.create(charger_id="CLI-REN-1", connector_id=None, display_name="Old")
        connector_a = Charger.objects.create(
            charger_id="CLI-REN-1", connector_id=1, display_name="Old A"
        )
        connector_b = Charger.objects.create(
            charger_id="CLI-REN-1", connector_id=2, display_name="Old B"
        )

        call_command("chargers", "--sn", "CLI-REN-1", "--rename", "Main Hub")

        connector_a.refresh_from_db()
        connector_b.refresh_from_db()
        self.assertEqual(connector_a.display_name, "Main Hub A")
        self.assertEqual(connector_b.display_name, "Main Hub B")

    def test_send_restart_registers_pending_call(self) -> None:
        """Restart requests send Reset and register timeout-tracked pending metadata."""

        charger = Charger.objects.create(charger_id="CLI-RST-1", connector_id=1)

        class DummyWs:
            def __init__(self) -> None:
                self.messages: list[str] = []

            async def send(self, payload: str) -> None:
                self.messages.append(payload)

        ws = DummyWs()

        with (
            patch("apps.ocpp.management.commands.chargers.store.get_connection", return_value=ws),
            patch("apps.ocpp.management.commands.chargers.store.schedule_call_timeout"),
        ):
            call_command("chargers", "--sn", "CLI-RST-1", "--cp", "A", "--send-restart")

        self.assertEqual(len(ws.messages), 1)
        frame = json.loads(ws.messages[0])
        self.assertEqual(frame[2], "Reset")

        metadata = store.pop_pending_call(frame[1])
        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(metadata.get("action"), "Reset")

    def test_charger_alias_defaults_to_base_charger(self) -> None:
        """The ``charger`` alias selects the default base charger without selectors."""

        base = Charger.objects.create(charger_id="CLI-ALIAS-1", connector_id=None)
        Charger.objects.create(charger_id="CLI-ALIAS-1", connector_id=1)

        call_command("charger", "--rename", "Alias Name")

        base.refresh_from_db()
        self.assertEqual(base.display_name, "Alias Name")
