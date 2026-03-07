"""Tests for the ``chargers`` management command websocket auth options."""

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

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
